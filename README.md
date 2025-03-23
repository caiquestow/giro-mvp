# GIRO MVP - Semi-Automatizado com Gupshup + Langchain

## Como rodar localmente

1. Crie um arquivo `.env` com sua chave da OpenAI:
```
OPENAI_API_KEY=your_openai_key
```

2. Instale as dependências:
```
pip install -r requirements.txt
```

3. Rode o servidor:
```
uvicorn main:app --reload --port 8000
```

4. Use `ngrok` ou `cloudflared` para expor sua porta local e configure o webhook no Gupshup:
```
ngrok http 8000
```

## Fluxo

- Recebe mensagem de texto do restaurante via Gupshup
- Processa com Langchain + GPT-4o
- Retorna sugestão prática sobre desperdício de alimentos