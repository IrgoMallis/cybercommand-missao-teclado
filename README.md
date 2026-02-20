# CyberCommand: Missao Teclado (Streamlit)

Aplicativo educativo em Python + Streamlit para treino de atalhos de teclado com foco em turmas iniciantes (12-16 anos).

## Funcionalidades

- escolha de 1, 2 ou 3 jogadores;
- turnos com divisao do tempo total (30 min);
- missoes progressivas com XP;
- campanha estendida (72 missoes) para aula de ~30 minutos;
- teclado ABNT2 em formato visual;
- modo "Aula Segura" (padrao) para ambiente web;
- relatorio PDF por turma/grupo;
- envio do PDF para GitHub.

## Estrutura

```text
cybercommand-missao-teclado/
  app.py
  requirements.txt
  README.md
```

## Executar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy gratuito (Streamlit Community Cloud)

1. Suba a pasta em um repositorio GitHub.
2. No Streamlit Cloud, conecte o repo.
3. Defina `app.py` como arquivo principal.
4. Adicione as secrets do GitHub (opcional para envio automatico de PDF):

```toml
GITHUB_OWNER = "seu-owner"
GITHUB_REPO = "seu-repo"
GITHUB_BRANCH = "main"
GITHUB_TOKEN = "ghp_xxx"
```

## Boas praticas

- nao commitar token no repositorio;
- usar `st.secrets` para credenciais;
- manter o modo "Aula Segura" ativo para aulas via navegador;
- testar fluxo completo (inicio -> jogo -> PDF -> upload).

## Limitacao tecnica importante

Mesmo em Streamlit, por rodar no navegador, atalhos de sistema como `Alt+Tab` e `Win+L` nao podem ser bloqueados 100% apenas pelo app.

