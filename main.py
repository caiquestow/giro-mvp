import os
import requests
from fastapi import FastAPI, Request
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.5,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

def extract_message_and_sender(data):
    try:
        # Detectar formato automaticamente
        if "entry" in data and "changes" in data["entry"][0]:
            # Meta format (v3)
            print("📦 Detectado formato: Meta (v3)")
            entry = data.get("entry", [])[0]
            change = entry.get("changes", [])[0]
            value = change.get("value", {})
            messages = value.get("messages", [])

            if not messages:
                return None, None

            msg = messages[0]
            text = msg.get("text", {}).get("body", "")
            sender = msg.get("from", "")
            return text, sender

        elif "payload" in data and "sender" in data.get("payload", {}):
            # Gupshup format (v1/v2)
            print("📦 Detectado formato: Gupshup (v1/v2)")
            message = data.get("payload", {}).get("payload", {}).get("text", "")
            sender = data.get("payload", {}).get("sender", {}).get("phone", "")
            return message, sender

        else:
            print("❌ Formato desconhecido")
            return None, None

    except Exception as e:
        print("🚨 Erro ao extrair mensagem:", e)
        return None, None

def send_to_user(para, texto):
    url = "https://api.gupshup.io/sm/api/v1/msg"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "apikey": os.getenv("GUPSHUP_API_KEY")
    }
    data = {
        "channel": "whatsapp",
        "source": os.getenv("GUPSHUP_SOURCE_NUMBER"),
        "destination": para,
        "message": texto
    }

    r = requests.post(url, headers=headers, data=data)
    print("Resposta enviada:", r.status_code, r.text)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message, sender = extract_message_and_sender(data)

    if not message:
        return {
            "message": {
                "type": "text",
                "text": "Mensagem não recebida corretamente."
            }
        }

    # response = llm.invoke([
    #     HumanMessage(content=f"Mensagem recebida de um restaurante: '{message}'. Dê uma sugestão prática e direta para reduzir o desperdício de alimentos.")
    # ])

    # print(response.content)
    # answer = response.content
    answer = "Testando resposta"
    send_to_user(sender, answer)

    return {
        "message": {
            "type": "text",
            "text": answer
        }
    }