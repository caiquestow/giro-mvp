from fastapi import FastAPI, Request
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.5,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message = data.get("message", {}).get("text", "")
    sender = data.get("sender", "")

    if not message:
        return {"message": {"text": "Mensagem não recebida corretamente."}}

    response = llm.invoke([
        HumanMessage(content=f"Mensagem recebida de um restaurante: '{message}'. Dê uma sugestão prática e direta para reduzir o desperdício de alimentos.")
    ])

    return {"message": {"text": response.content}}