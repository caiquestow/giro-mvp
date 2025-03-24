import os
import re
import logging
import requests
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from datetime import datetime, timedelta
from pymongo import MongoClient, ASCENDING, DESCENDING
from cloudinary.uploader import upload as cloudinary_upload
from cloudinary.utils import cloudinary_url
import cloudinary
import mimetypes
import tempfile
import urllib.request
import pandas as pd
import xml.etree.ElementTree as ET
import json

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

#######################################
# LOGGING ROBUSTO
#######################################
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

#######################################
# CARREGAR .env
#######################################
load_dotenv()

#######################################
# FASTAPI APP
#######################################
app = FastAPI()

#######################################
# CONFIGURA MODELO OPENAI
#######################################
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.5,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

#######################################
# CONEXÃO MONGODB
#######################################
mongo_client = MongoClient(os.getenv("MONGODB_URI"))
db = mongo_client["giroh_data"]
users_col = db["users"]
companies_col = db["companies"]
interactions_col = db["interactions"]
files_col = db["files"]
stock_col = db["stock"]
sales_col = db["sales"]
recipes_col = db["recipes"]
losses_col = db["losses"]
context_col = db["context_memory"]

# Criação de índices
interactions_col.create_index([("user_id", ASCENDING), ("timestamp", DESCENDING)])
files_col.create_index([("user_id", ASCENDING), ("type", ASCENDING)])
users_col.create_index("phone")

#######################################
# CONFIGURA CLOUDINARY
#######################################
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

#######################################
# FUNÇÃO DE ENVIO DE MENSAGEM
#######################################
def send_reply(to, text):
    url = "https://api.gupshup.io/sm/api/v1/msg"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "apikey": os.getenv("GUPSHUP_API_KEY")
    }
    data = {
        "channel": "whatsapp",
        "source": os.getenv("GUPSHUP_SOURCE_NUMBER"),
        "destination": to,
        "message": text
    }
    resp = requests.post(url, headers=headers, data=data)
    if resp.status_code != 200:
        logger.warning(f"Falha ao enviar msg Gupshup. Status: {resp.status_code} Resp: {resp.text}")

#######################################
# FUNÇÕES AUXILIARES
#######################################
def extract_message_and_sender(data):
    """
    Extrai o texto e o telefone do payload do Gupshup ou Meta, além de anexos.
    """
    try:
        if "entry" in data and "changes" in data["entry"][0]:
            entry = data["entry"][0]
            change = entry["changes"][0]
            value = change["value"]
            messages = value.get("messages", [])
            if not messages:
                return None, None, []
            msg = messages[0]
            text = msg.get("text", {}).get("body", "")
            sender = msg.get("from", "")
            attachments = []
            if "image" in msg:
                attachments.append({
                    "type": "image",
                    "url": msg["image"].get("link"),
                    "filename": msg["image"].get("caption", "image.jpg")
                })
            if "document" in msg:
                attachments.append({
                    "type": "document",
                    "url": msg["document"].get("link"),
                    "filename": msg["document"].get("filename", "file")
                })
            return text, sender, attachments

        elif "payload" in data and "sender" in data.get("payload", {}):
            message = data["payload"].get("payload", {}).get("text", "")
            sender = data["payload"].get("sender", {}).get("phone", "")
            attachments = []
            media = data["payload"].get("payload", {}).get("media")
            if media:
                attachments.append({
                    "type": media.get("type", "document"),
                    "url": media.get("url"),
                    "filename": media.get("caption", "file")
                })
            return message, sender, attachments

        return None, None, []
    except Exception as e:
        logger.error("Erro ao extrair mensagem", exc_info=True)
        return None, None, []

def upload_to_cloudinary(file_url, user_id, original_name):
    ext = os.path.splitext(original_name)[-1].lower()
    mime_type, _ = mimetypes.guess_type(original_name)
    public_id = f"users/{user_id}/{datetime.utcnow().isoformat()}"

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        urllib.request.urlretrieve(file_url, tmp.name)
        res = cloudinary_upload(tmp.name, public_id=public_id, resource_type="raw")
    try:
        os.unlink(tmp.name)
    except:
        pass

    return {
        "url": res.get("secure_url"),
        "type": ext.replace(".", ""),
        "mime_type": mime_type,
        "public_id": res.get("public_id"),
        "original_name": original_name
    }

def parse_file(file_path, file_type):
    try:
        if file_type in ["csv", "xls", "xlsx"]:
            df = pd.read_csv(file_path) if file_type == "csv" else pd.read_excel(file_path)
            return df.to_string(index=False)
        elif file_type == "xml":
            tree = ET.parse(file_path)
            root = tree.getroot()
            return ET.tostring(root, encoding='unicode')
        else:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
    except Exception as e:
        logger.error("Parse error", exc_info=True)
        return "Unable to parse content."

def analyze_file_content(content, filename):
    prompt = f"""
Você é um assistente que ajuda restaurantes a entender arquivos e documentos. O conteúdo abaixo foi extraído do arquivo '{filename}'.

Conteúdo:
{content}

Resuma o conteúdo e destaque possíveis informações úteis para um restaurante.
"""
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content
    except:
        return "Não foi possível analisar o conteúdo."

#######################################
# FUNÇÃO DE CLASSIFICAÇÃO DE INTENÇÃO
#######################################
def classify_intent(message: str):
    prompt = f"""
Você é um assistente inteligente para restaurantes. Um usuário enviou a seguinte mensagem:

\"\"\"{message}\"\"\"

Classifique a intenção principal da mensagem. Retorne apenas um dos seguintes intents como JSON:
- register_stock
- register_sales
- register_recipe
- register_loss
- weekly_summary_request
- request_recipe
- analyze_data
- send_file
- general_conversation

Formato:
{{"intent": "intent_aqui", "observation": "breve justificativa"}}
"""
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return json.loads(response.content)
    except:
        return {"intent": "general_conversation", "observation": "fallback"}

#######################################
# CRUD DE USUÁRIOS + ROLE
#######################################
def get_or_register_user(phone, message):
    """
    Se não existir user com esse phone, cria:
      - Nova company (name = message.strip())
      - Novo user com role=admin
    Retorna (company_id, is_new_user).
    """
    user = users_col.find_one({"phone": phone})
    if user:
        return user["company_id"], False

    company_name = message.strip()
    new_company = {"name": company_name, "created_at": datetime.utcnow()}
    company_id = companies_col.insert_one(new_company).inserted_id

    new_user = {
        "phone": phone,
        "name": "Administrador",
        "role": "admin",
        "company_id": company_id,
        "created_at": datetime.utcnow()
    }
    users_col.insert_one(new_user)
    return company_id, True

def check_role(phone, required_role="admin"):
    """
    Verifica se o usuário tem 'required_role'. Caso contrário, lança PermissionError.
    """
    user = users_col.find_one({"phone": phone})
    if not user:
        raise PermissionError("Usuário não encontrado")
    if user.get("role") != required_role:
        raise PermissionError(f"Você não tem permissão. Seu role: {user.get('role')} | Requerido: {required_role}")

#######################################
# REGEX PARA EXTRAIR CAMPOS
#######################################
def extract_field(text, field):
    """
    Retorna o valor após 'campo:' usando regex,
    ou None se não encontrar.
    Ex: field='produto', se tiver 'produto: arroz,'
    """
    pattern = rf"{field}\s*:\s*([^,]+)"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None

#######################################
# WEBHOOK
#######################################
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    # Extração de mensagem e sender
    message, sender, attachments = extract_message_and_sender(data)
    if not message:
        logger.info("Mensagem vazia ou não identificada.")
        return {"message": {"type": "text", "text": "Mensagem não recebida corretamente."}}

    # Verifica/cadastra usuário
    company_id, is_new_user = get_or_register_user(sender, message)
    if is_new_user:
        resposta = (
            "Bem-vindo ao GIRO! Sua empresa foi cadastrada com sucesso. "
            "Agora envie uma mensagem como: 'produto: tomate, quantidade: 5kg' para registrar seu estoque."
        )
        logger.info(f"Novo usuário criado: phone={sender}, company_id={company_id}")
        send_reply(sender, resposta)
        return {"message": {"type": "text", "text": resposta}}

    # Classifica intenção
    intent_info = classify_intent(message)
    intent = intent_info.get("intent", "general_conversation")
    logger.info(f"Intenção detectada: {intent} (obs: {intent_info.get('observation')})")

    resposta = "Recebido!"
    file_ids = []

    # Upload e parse de anexos (se existirem)
    for file in attachments:
        try:
            # faz upload
            file_data = upload_to_cloudinary(file["url"], sender, file["filename"])
            # parse e analise se quiser
            tmp_path = tempfile.NamedTemporaryFile(delete=False).name
            urllib.request.urlretrieve(file_data["url"], tmp_path)
            content = parse_file(tmp_path, file_data["type"])
            # analysis = analyze_file_content(content, file["filename"])
            os.unlink(tmp_path)
        except Exception as e:
            logger.error("Erro ao processar arquivo", exc_info=True)

    # 1) register_loss => role: admin
    if intent == "register_loss":
        try:
            check_role(sender, "admin")
            produto = extract_field(message, "produto") or ""
            quantidade = extract_field(message, "quantidade") or ""
            motivo = extract_field(message, "motivo") or "não informado"
            perda = {
                "user_id": sender,
                "company_id": company_id,
                "product": produto,
                "quantity": quantidade,
                "reason": motivo,
                "original_message": message,
                "timestamp": datetime.utcnow()
            }
            losses_col.insert_one(perda)
            resposta = f"Perda registrada: {produto} ({quantidade}) - Motivo: {motivo}"
        except PermissionError as pe:
            logger.warning(f"Permissão negada: {pe}")
            resposta = "Você não tem permissão para registrar perdas."

    # 2) register_stock => role: admin
    elif intent == "register_stock":
        try:
            check_role(sender, "admin")
            produto = extract_field(message, "produto") or message
            quantidade = extract_field(message, "quantidade") or ""
            validade = extract_field(message, "validade")
            estoque = {
                "user_id": sender,
                "company_id": company_id,
                "product": produto,
                "quantity": quantidade,
                "expiry_date": validade,
                "original_message": message,
                "timestamp": datetime.utcnow()
            }
            stock_col.insert_one(estoque)
            resposta = f"Estoque registrado: {produto} ({quantidade})"
        except PermissionError as pe:
            resposta = str(pe)

    # 3) register_sales => role: admin
    elif intent == "register_sales":
        try:
            check_role(sender, "admin")
            item = extract_field(message, "item") or message
            quantidade = extract_field(message, "quantidade") or ""
            venda = {
                "user_id": sender,
                "company_id": company_id,
                "item": item,
                "quantity": quantidade,
                "original_message": message,
                "timestamp": datetime.utcnow()
            }
            sales_col.insert_one(venda)
            resposta = f"Venda registrada: {item} ({quantidade})"
        except PermissionError as pe:
            resposta = str(pe)

    # 4) register_recipe => role: admin
    elif intent == "register_recipe":
        try:
            check_role(sender, "admin")
            produto = extract_field(message, "recipe") or "receita"
            ing_text = extract_field(message, "ingredients") or ""
            ingredientes = []
            for it in ing_text.split(","):
                partes = it.strip().split(" ")
                if len(partes) >= 2:
                    quantidade = partes[-1]
                    nome = " ".join(partes[:-1])
                    ingredientes.append({"name": nome, "quantity": quantidade})
            receita = {
                "user_id": sender,
                "company_id": company_id,
                "product": produto,
                "ingredients": ingredientes,
                "original_message": message,
                "timestamp": datetime.utcnow()
            }
            recipes_col.insert_one(receita)
            resposta = f"Ficha técnica registrada para {produto}!"
        except PermissionError as pe:
            resposta = str(pe)
        except Exception as e:
            logger.error("Erro ao processar ficha técnica", exc_info=True)
            resposta = "Não consegui entender o formato da ficha técnica."

    # 5) weekly_summary_request
    elif intent == "weekly_summary_request":
        sete_dias = datetime.utcnow() - timedelta(days=7)
        estoque = list(stock_col.find({"company_id": company_id, "timestamp": {"$gte": sete_dias}}))
        vendas = list(sales_col.find({"company_id": company_id, "timestamp": {"$gte": sete_dias}}))

        texto_estoque = "\n".join([f"- {s.get('product', '')}: {s.get('quantity', '')}" for s in estoque]) or "Sem estoque."
        texto_vendas = "\n".join([f"- {v.get('item', '')}: {v.get('quantity', '')}" for v in vendas]) or "Sem vendas."

        prompt = f"""
Você é um assistente de restaurantes. Gere um resumo da semana com base nos dados abaixo:

📦 Estoque:
{texto_estoque}

💰 Vendas:
{texto_vendas}
"""
        resp = llm.invoke([HumanMessage(content=prompt)])
        resposta = resp.content

        context_col.insert_one({
            "user_id": sender,
            "company_id": company_id,
            "summary": resposta,
            "type": "weekly_summary",
            "tags": ["resumo", "semana"],
            "created_at": datetime.utcnow()
        })

    # 6) request_recipe
    elif intent == "request_recipe":
        try:
            extracao_prompt = f"Qual produto o usuário quer ver a ficha técnica com base nesta frase: '{message}'? Responda apenas com o nome do produto."
            nome_produto = llm.invoke([HumanMessage(content=extracao_prompt)]).content.strip()
            receita = recipes_col.find_one({"company_id": company_id, "product": {"$regex": nome_produto, "$options": "i"}})
            if receita:
                lista_ing = "\n".join([f"- {i['name']}: {i['quantity']}" for i in receita.get("ingredients", [])])
                resposta = f"📄 Ficha técnica de {receita['product']}\n{lista_ing}"
            else:
                resposta = f"Não encontrei ficha técnica para '{nome_produto}'."
        except Exception as e:
            logger.error("Erro ao buscar receita", exc_info=True)
            resposta = "Não consegui buscar a ficha técnica no momento."

    # 7) analyze_data
    elif intent == "analyze_data":
        estoque = list(stock_col.find({"company_id": company_id}).sort("timestamp", -1).limit(5))
        vendas = list(sales_col.find({"company_id": company_id}).sort("timestamp", -1).limit(5))

        texto_estoque = "\n".join([f"- {s.get('product','')}: {s.get('quantity','')}" for s in estoque]) or "Sem estoque."
        texto_vendas = "\n".join([f"- {v.get('item','')}: {v.get('quantity','')}" for v in vendas]) or "Sem vendas."

        prompt = f"""
Você é um assistente de restaurantes. Com base nas informações abaixo, gere sugestões para reduzir desperdícios e aumentar o lucro:

📦 Estoque:
{texto_estoque}

💰 Vendas:
{texto_vendas}
"""
        resp = llm.invoke([HumanMessage(content=prompt)])
        analise = resp.content

        context_col.insert_one({
            "user_id": sender,
            "company_id": company_id,
            "summary": analise,
            "type": "analysis_result",
            "tags": ["análise", "automática"],
            "created_at": datetime.utcnow()
        })
        resposta = analise

    # 8) general_conversation
    elif intent == "general_conversation":
        resposta = "Tudo certo! Você pode registrar estoque, vendas, perdas ou pedir uma análise."

    # 9) send_file
    elif intent == "send_file":
        resposta = "Arquivo recebido. Ele será analisado em breve."

    # Salva a interação
    interactions_col.insert_one({
        "user_id": sender,
        "company_id": company_id,
        "message": message,
        "intention": intent,
        "response": resposta,
        "timestamp": datetime.utcnow(),
        "attachments": file_ids,
        "context_used": []
    })

    logger.info(f"Resposta final: {resposta[:60]}...")  # log parcial
    send_reply(sender, resposta)
    return {"message": {"type": "text", "text": resposta}}

########################################
# FIM DO CÓDIGO
########################################
