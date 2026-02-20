from __future__ import annotations

import base64
import io
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

import requests
import streamlit as st
import streamlit.components.v1 as components
from fpdf import FPDF
from streamlit.errors import StreamlitSecretNotFoundError


PHASE_REGEX = re.compile(r"Fase\s+(\d)")
DANGEROUS_COMBOS = {
    "Ctrl+W",
    "Ctrl+T",
    "Ctrl+Shift+T",
    "Ctrl+R",
    "F5",
    "Ctrl+N",
    "Ctrl+L",
    "Alt+F4",
    "Alt+Tab",
    "Win+L",
}

DEFAULT_GITHUB_OWNER = "IrgoMallis"
DEFAULT_GITHUB_REPO = "cybercommand-missao-teclado"
DEFAULT_GITHUB_BRANCH = "master"


def sanitize_secret(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    if cleaned.startswith(("'", '"')) and cleaned.endswith(("'", '"')) and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned


@dataclass
class Mission:
    phase: str
    label: str
    real_combo: str
    safe_combo: str
    keys: List[str]
    xp: int
    task_type: str


MISSIONS = [
    Mission("Fase 1 - Texto", "Copiar texto da origem", "Ctrl+C", "Ctrl+C", ["Ctrl", "C"], 10, "copy"),
    Mission("Fase 1 - Texto", "Colar texto no destino", "Ctrl+V", "Ctrl+V", ["Ctrl", "V"], 10, "paste"),
    Mission("Fase 1 - Texto", "Selecionar todo o texto no destino", "Ctrl+A", "Ctrl+A", ["Ctrl", "A"], 12, "select_all"),
    Mission("Fase 1 - Texto", "Recortar texto selecionado no destino", "Ctrl+X", "Ctrl+X", ["Ctrl", "X"], 12, "cut"),
    Mission("Fase 1 - Texto", "Desfazer recorte no destino", "Ctrl+Z", "Ctrl+Z", ["Ctrl", "Z"], 12, "undo"),
    Mission("Fase 1 - Texto", "Colar sem formatacao", "Ctrl+Shift+V", "Ctrl+Shift+V", ["Ctrl", "Shift", "V"], 14, "paste_plain"),
]


def css() -> None:
    st.markdown(
        """
        <style>
          .block-container {padding-top: 1.2rem; max-width: 1080px;}
          .title {font-family: Consolas, monospace; color: #b8f7b8; text-shadow: 0 0 8px #34d399;}
          .terminal-box {
            border: 1px solid #2a7f2a; border-radius: 10px; padding: 12px;
            background: #030a03; color: #90ff90; font-family: Consolas, monospace;
          }
          .hint {font-size: 0.88rem; color: #c4b5fd;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def init_state() -> None:
    defaults = {
        "stage": "start",
        "players_count": 1,
        "players": [],
        "current_player": 0,
        "mission_idx": 0,
        "total_xp": 0,
        "safe_mode": True,
        "game_started_at": None,
        "mission_started_at": None,
        "finish_reason": "",
        "turma_grupo": "",
        "alunos": "",
        "feedback": "",
        "teacher_cfg": {},
        "clipboard_virtual": "",
        "source_text": "Texto de treino: copie este paragrafo com Ctrl+C e cole no bloco de destino usando Ctrl+V.",
        "destination_text": "",
        "editor_box": "",
        "final_box": "",
        "undo_stack": [],
        "sim_tabs": 1,
        "active_window": "Editor",
        "show_desktop": False,
        "locked_screen": False,
        "security_menu_open": False,
        "action_log": "",
        "source_initial": "",
        "undo_target": "",
        "mission_ctx_idx": -1,
        "last_mission_idx": -1,
        "report_auto_sent": False,
        "report_auto_status": "",
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def normalize_combo(text: str) -> str:
    raw = re.split(r"[+\s]+", text.strip())
    raw = [token for token in raw if token]
    if not raw:
        return ""
    mapping = {
        "control": "Ctrl",
        "ctrl": "Ctrl",
        "alt": "Alt",
        "shift": "Shift",
        "win": "Win",
        "meta": "Win",
        "del": "Del",
        "delete": "Del",
        "tab": "Tab",
        "esc": "Esc",
        "escape": "Esc",
        "setadireita": "Right",
        "direita": "Right",
        "right": "Right",
        "setaesquerda": "Left",
        "esquerda": "Left",
        "left": "Left",
        "setacima": "Up",
        "cima": "Up",
        "up": "Up",
        "setabaixo": "Down",
        "baixo": "Down",
        "down": "Down",
        "inicio": "Home",
        "home": "Home",
        "fim": "End",
        "end": "End",
        "windows": "Win",
    }
    norm = []
    for token in raw:
        key = mapping.get(token.lower(), token.upper() if len(token) == 1 else token.capitalize())
        norm.append(key)

    modifiers = [m for m in ["Ctrl", "Alt", "Shift", "Win"] if m in norm]
    rest = [k for k in norm if k not in {"Ctrl", "Alt", "Shift", "Win"}]
    return "+".join(modifiers + rest)


def init_players(count: int) -> List[Dict]:
    return [
        {
            "id": idx + 1,
            "xp": 0,
            "hits": 0,
            "attempts": 0,
            "errors": 0,
            "phase_hits": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
            "mission_times": [],
        }
        for idx in range(count)
    ]


def expected_combo(mission: Mission) -> str:
    # Em Streamlit (web), validamos o atalho digitado literalmente como no mundo real.
    return mission.real_combo


def pretty_key(key: str) -> str:
    mapping = {
        "Right": "→",
        "Left": "←",
        "Up": "↑",
        "Down": "↓",
    }
    return mapping.get(key, key)


def pretty_combo(combo: str) -> str:
    return "+".join(pretty_key(part) for part in combo.split("+"))


def is_dangerous_combo(combo: str) -> bool:
    return combo in DANGEROUS_COMBOS


def current_mission() -> Mission:
    return MISSIONS[st.session_state.mission_idx]


def init_simulation_env() -> None:
    st.session_state.clipboard_virtual = ""
    st.session_state.source_text = "Texto de treino: copie este paragrafo com Ctrl+C e cole no bloco de destino usando Ctrl+V."
    st.session_state.destination_text = ""
    st.session_state.undo_stack = []
    st.session_state.sim_tabs = 1
    st.session_state.active_window = "Editor"
    st.session_state.show_desktop = False
    st.session_state.locked_screen = False
    st.session_state.security_menu_open = False
    st.session_state.action_log = "Simulador iniciado."
    st.session_state.source_initial = "Texto base: pratique os comandos de edicao nesta atividade."
    st.session_state.undo_target = ""
    st.session_state.mission_ctx_idx = -1
    st.session_state.editor_box = ""
    st.session_state.final_box = ""


def prepare_mission_context(mission: Mission, mission_idx: int) -> None:
    if st.session_state.mission_ctx_idx == st.session_state.current_player:
        return

    samples = [
        "A tecnologia move o mundo.",
        "Aprender atalhos aumenta a produtividade.",
        "Cada comando economiza segundos preciosos.",
        "Praticar diariamente gera autonomia digital.",
        "Copiar, colar e desfazer sao superpoderes basicos.",
    ]
    sample = samples[st.session_state.current_player % len(samples)]

    st.session_state.source_text = sample
    st.session_state.source_initial = sample
    st.session_state.destination_text = ""
    st.session_state.editor_box = ""
    st.session_state.final_box = ""
    st.session_state.undo_target = ""
    st.session_state.action_log = f"Contexto carregado para o Jogador {st.session_state.current_player + 1}."
    st.session_state.mission_ctx_idx = st.session_state.current_player


def validate_mission_by_result(mission: Mission, source_value: str, editor_value: str, final_value: str) -> tuple[bool, str]:
    src_now = source_value.strip()
    editor_now = editor_value.strip()
    final_now = final_value.strip()
    src_initial = st.session_state.source_initial.strip()

    if mission.task_type == "copy":
        ok = src_now == src_initial
        msg = "Para validar, selecione na ORIGEM e pressione Ctrl+C."
        return ok, msg
    if mission.task_type == "paste":
        ok = editor_now == src_initial
        msg = "Para validar, cole o texto da origem na CAIXA DE TRABALHO com Ctrl+V."
        return ok, msg
    if mission.task_type == "select_all":
        ok = True
        msg = "Use Ctrl+A na caixa de trabalho e clique em validar."
        return ok, msg
    if mission.task_type == "cut":
        ok = editor_now == ""
        msg = "Para validar, use Ctrl+A e Ctrl+X na caixa de trabalho para esvaziar o texto."
        return ok, msg
    if mission.task_type == "undo":
        ok = True
        msg = "Use Ctrl+Z na caixa de trabalho e clique em validar."
        return ok, msg
    if mission.task_type == "paste_plain":
        ok = final_now == src_initial
        msg = "Para validar, copie da origem e cole sem formatacao na CAIXA FINAL com Ctrl+Shift+V."
        return ok, msg
    return False, "Missao desconhecida."


def apply_simulation_effect(combo: str) -> None:
    if combo == "Alt+Tab":
        st.session_state.active_window = "Navegador" if st.session_state.active_window == "Editor" else "Editor"
        st.session_state.show_desktop = False
        st.session_state.action_log = f"Alt+Tab executado: foco trocado para {st.session_state.active_window}."
    elif combo == "Ctrl+T":
        st.session_state.sim_tabs += 1
        st.session_state.action_log = f"Ctrl+T executado: nova aba virtual aberta ({st.session_state.sim_tabs})."
    elif combo == "Ctrl+W":
        st.session_state.sim_tabs = max(1, st.session_state.sim_tabs - 1)
        st.session_state.action_log = f"Ctrl+W executado: aba virtual fechada ({st.session_state.sim_tabs})."
    elif combo == "Win+D":
        st.session_state.show_desktop = True
        st.session_state.action_log = "Win+D executado: desktop virtual mostrado."
    elif combo == "Win+L":
        st.session_state.locked_screen = True
        st.session_state.action_log = "Win+L executado: tela virtual bloqueada."
    elif combo == "Ctrl+Alt+Del":
        st.session_state.security_menu_open = True
        st.session_state.action_log = "Ctrl+Alt+Del executado: menu de seguranca virtual aberto."
    elif combo == "Win+E":
        st.session_state.active_window = "Explorador"
        st.session_state.action_log = "Win+E executado: explorador de arquivos virtual aberto."
    elif combo == "Win+I":
        st.session_state.active_window = "Configuracoes"
        st.session_state.action_log = "Win+I executado: configuracoes virtuais abertas."
    elif combo == "Ctrl+Shift+Esc":
        st.session_state.active_window = "Gerenciador de Tarefas"
        st.session_state.action_log = "Ctrl+Shift+Esc executado: gerenciador de tarefas virtual aberto."
    elif combo == "Alt+F4":
        st.session_state.active_window = "Editor"
        st.session_state.action_log = "Alt+F4 executado: janela ativa virtual fechada."
    elif combo == "Win+Shift+S":
        st.session_state.action_log = "Win+Shift+S executado: captura de area virtual registrada."
    elif combo == "Ctrl+Shift+T":
        st.session_state.sim_tabs += 1
        st.session_state.action_log = f"Ctrl+Shift+T executado: aba virtual reaberta ({st.session_state.sim_tabs})."
    elif combo in {"Ctrl+L", "Ctrl+R", "F5", "Ctrl+S", "Ctrl+F", "Ctrl+A", "Ctrl+Y", "Ctrl+Shift+V"}:
        st.session_state.action_log = f"{combo} executado no simulador."
    elif combo in {"Win+Right", "Win+Left", "Win+Up", "Win+M", "Ctrl+Esc"}:
        st.session_state.action_log = f"{combo} executado: gerenciamento de janela/menu virtual atualizado."
    elif combo in {"Ctrl+Right", "Ctrl+Left", "Ctrl+Shift+Right", "Ctrl+Shift+Left", "Home", "End"}:
        st.session_state.action_log = f"{combo} executado: navegacao de texto virtual aplicada."


def phase_number(phase_text: str) -> str:
    match = PHASE_REGEX.search(phase_text)
    return match.group(1) if match else "1"


def get_total_duration_seconds() -> int:
    start = st.session_state.game_started_at
    if not start:
        return 0
    return int((datetime.now() - start).total_seconds())


def advance_player_turn() -> None:
    if st.session_state.players_count <= 1:
        return
    st.session_state.current_player = (st.session_state.current_player + 1) % st.session_state.players_count


def finish_game(reason: str) -> None:
    st.session_state.stage = "end"
    st.session_state.finish_reason = reason


def register_mission_success(player: Dict, mission: Mission) -> None:
    player["hits"] += 1
    player["xp"] += mission.xp
    st.session_state.total_xp += mission.xp
    delta = (datetime.now() - st.session_state.mission_started_at).total_seconds()
    player["mission_times"].append(delta)
    ph = phase_number(mission.phase)
    if ph not in player["phase_hits"]:
        player["phase_hits"][ph] = 0
    player["phase_hits"][ph] += 1

    finished_player_list = st.session_state.mission_idx >= (len(MISSIONS) - 1)
    if finished_player_list:
        if st.session_state.players_count > 1 and st.session_state.current_player < (st.session_state.players_count - 1):
            next_player = st.session_state.current_player + 1
            st.session_state.current_player = next_player
            st.session_state.mission_idx = 0
            st.session_state.mission_ctx_idx = -1
            st.session_state.last_mission_idx = -1
            st.session_state.feedback = (
                f"Lista concluida pelo Jogador {player['id']}! TROCA DE PILOTO! Vez do Jogador {next_player + 1}."
            )
        else:
            finish_game("Todos os alunos da equipe concluíram a lista de comandos.")
            st.session_state.feedback = "Parabens! Atividade concluida por toda a equipe."
    else:
        st.session_state.mission_idx += 1
        st.session_state.feedback = "Acerto critico! Missao concluida."

    st.session_state.mission_started_at = datetime.now()


def build_pdf_bytes(report: Dict) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", size=12)

    def line(txt: str, gap: int = 8) -> None:
      pdf.cell(0, gap, txt=txt, ln=True)

    line("CyberCommand: Missao Teclado - Relatorio", 10)
    line(f"Gerado em: {report['generated_at']}")
    line(f"Turma/Grupo: {report['turma_grupo']}")
    line(f"Alunos: {', '.join(report['alunos'])}")
    line(f"Modo: {'Aula Segura' if report['safe_mode'] else 'Real'}")
    line(f"Missoes: {report['missions_completed']}/{report['missions_total']}")
    line(f"XP Total: {report['total_xp']}")
    line(f"Duracao: {report['duration_sec']} segundos", 10)
    line("Desempenho por jogador:", 10)

    for p in report["players"]:
        line(f"Jogador {p['id']} -> XP {p['xp']} | Acertos {p['hits']} | Tentativas {p['attempts']} | Erros {p['errors']}")
        line(f"Precisao {p['accuracy']}% | Tempo medio {p['avg_time']}s | Velocidade {p['velocity']} missoes/min")
        line(f"Fases: F1 {p['f1']} | F2 {p['f2']} | F3 {p['f3']} | F4 {p['f4']} | F5 {p['f5']}", 10)

    return bytes(pdf.output(dest="S"))


def build_report() -> Dict:
    duration_sec = max(1, get_total_duration_seconds())
    students = [name.strip() for name in st.session_state.alunos.splitlines() if name.strip()]
    students = students or ["Nao informado"]

    players_data = []
    for p in st.session_state.players:
        avg_time = sum(p["mission_times"]) / len(p["mission_times"]) if p["mission_times"] else 0
        accuracy = round((p["hits"] / p["attempts"]) * 100, 1) if p["attempts"] else 0
        velocity = round(p["hits"] / (duration_sec / 60), 2)
        players_data.append(
            {
                "id": p["id"],
                "xp": p["xp"],
                "hits": p["hits"],
                "attempts": p["attempts"],
                "errors": p["errors"],
                "accuracy": accuracy,
                "avg_time": round(avg_time, 2),
                "velocity": velocity,
                "f1": p["phase_hits"]["1"],
                "f2": p["phase_hits"]["2"],
                "f3": p["phase_hits"]["3"],
                "f4": p["phase_hits"]["4"],
                "f5": p["phase_hits"]["5"],
            }
        )

    total_completed = sum(p["hits"] for p in st.session_state.players)
    total_target = len(MISSIONS) * max(1, st.session_state.players_count)

    return {
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "turma_grupo": st.session_state.turma_grupo or "Nao informado",
        "alunos": students,
        "safe_mode": st.session_state.safe_mode,
        "missions_completed": total_completed,
        "missions_total": total_target,
        "total_xp": st.session_state.total_xp,
        "duration_sec": duration_sec,
        "players": players_data,
    }


def github_config_from_secrets() -> Dict | None:
    required = ["GITHUB_TOKEN"]
    try:
        if all(k in st.secrets for k in required):
            token = sanitize_secret(st.secrets["GITHUB_TOKEN"])
            if not token:
                return None
            return {
                "owner": sanitize_secret(st.secrets.get("GITHUB_OWNER", DEFAULT_GITHUB_OWNER)) or DEFAULT_GITHUB_OWNER,
                "repo": sanitize_secret(st.secrets.get("GITHUB_REPO", DEFAULT_GITHUB_REPO)) or DEFAULT_GITHUB_REPO,
                "token": token,
                "branch": sanitize_secret(st.secrets.get("GITHUB_BRANCH", DEFAULT_GITHUB_BRANCH)) or DEFAULT_GITHUB_BRANCH,
            }
    except StreamlitSecretNotFoundError:
        return None
    return None


def github_config_auto() -> Dict | None:
    cfg = github_config_from_secrets()
    if cfg:
        return cfg

    env_token = sanitize_secret(os.getenv("GITHUB_TOKEN", ""))
    if env_token:
        return {
            "owner": sanitize_secret(os.getenv("GITHUB_OWNER", DEFAULT_GITHUB_OWNER)) or DEFAULT_GITHUB_OWNER,
            "repo": sanitize_secret(os.getenv("GITHUB_REPO", DEFAULT_GITHUB_REPO)) or DEFAULT_GITHUB_REPO,
            "branch": sanitize_secret(os.getenv("GITHUB_BRANCH", DEFAULT_GITHUB_BRANCH)) or DEFAULT_GITHUB_BRANCH,
            "token": env_token,
        }

    try:
        token = subprocess.check_output(
            ["gh", "auth", "token"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        token = sanitize_secret(token)
        if token:
            return {
                "owner": DEFAULT_GITHUB_OWNER,
                "repo": DEFAULT_GITHUB_REPO,
                "branch": DEFAULT_GITHUB_BRANCH,
                "token": token,
            }
    except Exception:
        return None

    return None


def upload_pdf_to_github(pdf_bytes: bytes, filename: str, cfg: Dict) -> str:
    path = f"relatorios-cybercommand/{filename}"
    content = base64.b64encode(pdf_bytes).decode("utf-8")
    url = f"https://api.github.com/repos/{cfg['owner']}/{cfg['repo']}/contents/{path}"
    payload = {
        "message": f"docs: adiciona relatorio {filename}",
        "content": content,
        "branch": cfg.get("branch", "main"),
    }
    common_headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Tentativa 1: Bearer (funciona para a maioria dos tokens modernos)
    response = requests.put(
        url,
        headers={**common_headers, "Authorization": f"Bearer {cfg['token']}"},
        json=payload,
        timeout=30,
    )
    # Tentativa 2: token (compatibilidade com PAT clássico)
    if response.status_code == 401:
        response = requests.put(
            url,
            headers={**common_headers, "Authorization": f"token {cfg['token']}"},
            json=payload,
            timeout=30,
        )
    if response.status_code >= 300:
        raise RuntimeError(f"GitHub API {response.status_code}: {response.text}")
    data = response.json()
    return data.get("content", {}).get("html_url", "")


def render_keyboard_abnt2(highlight_keys: List[str] | None = None) -> None:
    highlight = set(highlight_keys or [])

    def k(label: str) -> str:
        cls = "background:#22c55e;color:#052e16;font-weight:700;border-color:#22c55e;" if label in highlight else "background:#111827;color:#e5e7eb;"
        shown = pretty_key(label)
        return f"<span style='display:inline-block;min-width:48px;text-align:center;padding:2px 6px;margin:1px;border:1px solid #374151;border-radius:4px;{cls}'>{shown}</span>"

    html = f"""
    <div style="border:1px solid #334155;border-radius:8px;padding:8px;background:#020617;overflow:auto;white-space:nowrap;">
      <div>{k('Esc')} {k('F4')} {k('F5')} {k("'")} {k('1')} {k('2')} {k('3')} {k('4')} {k('5')} {k('6')} {k('7')} {k('8')} {k('9')} {k('0')} {k('-')} {k('=')} {k('Backspace')}</div>
      <div>{k('Tab')} {k('Q')} {k('W')} {k('E')} {k('R')} {k('T')} {k('Y')} {k('U')} {k('I')} {k('O')} {k('P')} {k('[')} {k(']')} {k('\\\\')}</div>
      <div>{k('Caps')} {k('A')} {k('S')} {k('D')} {k('F')} {k('G')} {k('H')} {k('J')} {k('K')} {k('L')} {k('Ç')} {k('~')} {k('Enter')}</div>
      <div>{k('Shift')} {k('\\\\')} {k('Z')} {k('X')} {k('C')} {k('V')} {k('B')} {k('N')} {k('M')} {k(',')} {k('.')} {k('/')} {k('Shift')}</div>
      <div>{k('Ctrl')} {k('Win')} {k('Alt')} {k('Space')} {k('AltGr')} {k('Win')} {k('Menu')} {k('Ctrl')} {k('Del')} {k('Home')} {k('End')} {k('Left')} {k('Up')} {k('Down')} {k('Right')}</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_simulation_panel(mission: Mission) -> tuple[str, str, str]:
    st.markdown("### Simulador de comandos (janela virtual)")
    source_value = st.session_state.source_initial
    editor_value = st.session_state.get("editor_box", "")
    final_value = st.session_state.get("final_box", "")
    if not source_value.strip():
        source_value = "Texto de treino nao carregado. Clique em Jogar novamente para reiniciar a atividade."
        st.session_state.source_initial = source_value

    if mission.task_type == "copy":
        st.markdown("**Texto para copiar:**")
        st.code(source_value)
        st.text_area(
            "Caixa de origem (selecione tudo e pressione Ctrl+C)",
            value=source_value,
            height=100,
            disabled=True,
        )
    elif mission.task_type == "paste":
        st.markdown("**Texto de referência (copie daqui):**")
        st.code(source_value)
        st.text_area(
            "Caixa de origem (referencia)",
            value=source_value,
            height=90,
            disabled=True,
        )
        editor_value = st.text_area(
            "Caixa de trabalho (clique aqui e use Ctrl+V)",
            value=st.session_state.get("editor_box", ""),
            key="editor_box",
            height=110,
        )
    elif mission.task_type in {"select_all", "cut", "undo"}:
        editor_value = st.text_area(
            "Caixa de trabalho (faça a acao desta missao aqui)",
            value=st.session_state.get("editor_box", ""),
            key="editor_box",
            height=120,
        )
    elif mission.task_type == "paste_plain":
        st.markdown("**Texto de referência (copie daqui):**")
        st.code(source_value)
        st.text_area(
            "Caixa de origem (copie daqui)",
            value=source_value,
            height=90,
            disabled=True,
        )
        final_value = st.text_area(
            "Caixa final (clique aqui e use Ctrl+Shift+V)",
            value=st.session_state.get("final_box", ""),
            key="final_box",
            height=110,
        )
    else:
        st.info("Sem atividade de texto para esta missao.")

    st.code(st.session_state.action_log or "Aguardando acao do aluno...")
    return source_value, editor_value, final_value


def render_start() -> None:
    st.markdown("<h1 class='title'>CyberCommand: Missao Teclado (Streamlit)</h1>", unsafe_allow_html=True)
    st.markdown(
        "<div class='terminal-box'>Modo web (link): para evitar abrir novas abas/janelas, use 'Aula Segura'.</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"Atividade de hoje: {len(MISSIONS)} comandos de texto por aluno."
    )
    with st.expander("Lista completa de atalhos da atividade"):
        st.markdown(
            """
            **Lista da atividade de hoje**
            - Ctrl+C: copiar texto
            - Ctrl+V: colar texto
            - Ctrl+X: recortar texto
            - Ctrl+Z: desfazer
            - Ctrl+A: selecionar tudo
            - Ctrl+Shift+V: colar sem formatacao
            """
        )
    col1, col2 = st.columns(2)
    with col1:
        players_count = st.radio("Quantidade de jogadores", [1, 2, 3], horizontal=True, index=0)
        turma_grupo = st.text_input("Turma / Grupo", value=st.session_state.turma_grupo, placeholder="Ex: 8A - Grupo 2")
    with col2:
        alunos = st.text_area("Nomes dos alunos do grupo (um por linha)", value=st.session_state.alunos, height=130)
        safe_mode = st.toggle("Modo Aula Segura (recomendado)", value=True)

    if st.button("Iniciar Missao", type="primary", use_container_width=True):
        st.session_state.players_count = players_count
        st.session_state.turma_grupo = turma_grupo.strip()
        st.session_state.alunos = alunos
        st.session_state.safe_mode = safe_mode
        st.session_state.players = init_players(players_count)
        st.session_state.current_player = 0
        st.session_state.mission_idx = 0
        st.session_state.total_xp = 0
        st.session_state.game_started_at = datetime.now()
        st.session_state.mission_started_at = datetime.now()
        st.session_state.feedback = ""
        st.session_state.report_auto_sent = False
        st.session_state.report_auto_status = ""
        init_simulation_env()
        st.session_state.stage = "game"
        st.rerun()


def render_game() -> None:
    if st.session_state.last_mission_idx != st.session_state.mission_idx:
        st.session_state.last_mission_idx = st.session_state.mission_idx
        st.markdown(
            "<script>window.scrollTo({top: 0, behavior: 'smooth'});</script>",
            unsafe_allow_html=True,
        )

    mission = current_mission()
    prepare_mission_context(mission, st.session_state.mission_idx)
    expected = expected_combo(mission)
    inject_browser_shortcut_guard(
        expected_combo=expected,
        require_combo_before_validate=True,
    )
    player_idx = st.session_state.current_player
    player = st.session_state.players[player_idx]

    st.subheader("Sala de Comando")
    c1, c2, c3 = st.columns(3)
    c1.metric("Turno atual", f"Jogador {player_idx + 1}")
    total_done = sum(p["hits"] for p in st.session_state.players)
    total_target = len(MISSIONS) * st.session_state.players_count
    c2.metric("Exercicios concluidos", f"{total_done}/{total_target}")
    c3.metric("XP total", st.session_state.total_xp)
    progress = total_done / max(1, total_target)
    st.progress(progress, text=f"Missao atual do jogador: {st.session_state.mission_idx + 1}/{len(MISSIONS)}")

    st.markdown(f"### {mission.phase}")
    st.caption(f"Passo {st.session_state.mission_idx + 1} de {len(MISSIONS)}")
    st.write(f"Missao: **Aperte exatamente `{pretty_combo(expected)}` para {mission.label.lower()}**")
    if mission.task_type == "copy":
        st.info("Instrucao: selecione TODO o texto da caixa de origem e pressione Ctrl+C.")
    elif mission.task_type == "paste":
        st.info("Instrucao: clique na caixa de trabalho e pressione Ctrl+V para colar.")
    elif mission.task_type == "cut":
        st.info("Instrucao: com o texto selecionado na caixa de trabalho, pressione Ctrl+X.")
    elif mission.task_type == "undo":
        st.info("Instrucao: pressione Ctrl+Z na caixa de trabalho e depois valide.")
    elif mission.task_type == "select_all":
        st.info("Instrucao: clique na caixa de trabalho, pressione Ctrl+A e depois valide.")
    elif mission.task_type == "paste_plain":
        st.info("Instrucao: copie da origem e cole na caixa final com Ctrl+Shift+V.")
    elif mission.task_type == "confirm":
        st.info(
            f"Instrucao literal: execute `{pretty_combo(expected)}` e depois clique no botao de validacao."
        )
        if is_dangerous_combo(expected):
            st.warning(
                f"`{pretty_combo(expected)}` pode tirar o aluno da atividade no navegador. "
                "Use o botao protegido abaixo para validar sem sair da tela."
            )
    if st.session_state.feedback:
        st.info(st.session_state.feedback)

    render_keyboard_abnt2(mission.keys)
    st.caption(f"Teclas para pressionar: {', '.join(pretty_key(k) for k in mission.keys)}")

    # Ordem solicitada: enunciado -> atividade -> validacao
    render_simulation_panel(mission)

    if mission.task_type == "confirm":
        shown_expected = pretty_combo(expected)
        btn_label = (
            f"Validar comando protegido: {shown_expected}"
            if is_dangerous_combo(expected)
            else f"Apertei {shown_expected}, validar"
        )
        if st.button(btn_label, use_container_width=True):
            player["attempts"] += 1
            apply_simulation_effect(expected)
            register_mission_success(player, mission)
            st.rerun()
    elif mission.task_type == "select_all":
        if st.button("Apertei Ctrl+A, validar", use_container_width=True):
            player["attempts"] += 1
            register_mission_success(player, mission)
            st.rerun()
    elif mission.task_type == "undo":
        if st.button("Apertei Ctrl+Z, validar", use_container_width=True):
            player["attempts"] += 1
            register_mission_success(player, mission)
            st.rerun()
    else:
        with st.form("mission_form"):
            submitted = st.form_submit_button("Validar licao")
            if submitted:
                player["attempts"] += 1
                ok, hint = validate_mission_by_result(
                    mission,
                    st.session_state.source_initial,
                    st.session_state.get("editor_box", ""),
                    st.session_state.get("final_box", ""),
                )
                if ok:
                    register_mission_success(player, mission)
                    st.rerun()
                else:
                    player["errors"] += 1
                    st.session_state.feedback = f"Ainda nao validou. {hint}"
                    st.rerun()

    st.markdown("### Placar")
    for p in st.session_state.players:
        accuracy = round((p["hits"] / p["attempts"]) * 100, 1) if p["attempts"] else 0
        st.write(f"- Jogador {p['id']}: XP {p['xp']} | Acertos {p['hits']} | Precisao {accuracy}%")


def inject_browser_shortcut_guard(expected_combo: str = "", require_combo_before_validate: bool = False) -> None:
    expected_combo_js = expected_combo.replace("\\", "\\\\").replace("'", "\\'")
    require_flag = "true" if require_combo_before_validate else "false"
    components.html(
        f"""
        <script>
        (function () {{
          try {{
            const parentWin = window.parent;
            const doc = parentWin.document;
            const expectedCombo = '{expected_combo_js}';
            const requireCombo = {require_flag};

            function normalizeCombo(e) {{
              const parts = [];
              if (e.ctrlKey) parts.push("Ctrl");
              if (e.altKey) parts.push("Alt");
              if (e.shiftKey) parts.push("Shift");
              if (e.metaKey) parts.push("Win");

              const keyMap = {{
                "Control": "Ctrl",
                "Alt": "Alt",
                "Shift": "Shift",
                "Meta": "Win",
                "OS": "Win",
                "Escape": "Esc",
                "Esc": "Esc",
                "Delete": "Del",
                "Del": "Del",
                "Tab": "Tab",
                "ArrowRight": "Right",
                "ArrowLeft": "Left",
                "ArrowUp": "Up",
                "ArrowDown": "Down",
                " ": "Space",
              }};

              let key = keyMap[e.key] || e.key;
              if (!["Ctrl", "Alt", "Shift", "Win"].includes(key)) {{
                if (key.length === 1) key = key.toUpperCase();
                parts.push(key);
              }}
              return parts.join("+");
            }}

            function isBlockedCombo(combo) {{
              const blocked = new Set([
                "Ctrl+W", "Ctrl+T", "Ctrl+Shift+T", "Ctrl+R", "Ctrl+N", "Ctrl+L", "F5", "Ctrl+F4", "Alt+F4"
              ]);
              return blocked.has(combo);
            }}

            if (!parentWin.__cyber_guard_installed) {{
              parentWin.__cyber_guard_installed = true;
              parentWin.__cyber_combo_ok = false;
              parentWin.__cyber_expected_combo = "";
              parentWin.__cyber_require_combo = false;

              parentWin.__cyber_guard_handler = function (e) {{
                const combo = normalizeCombo(e);
                parentWin.__cyber_last_combo = combo;

                if (parentWin.__cyber_expected_combo && combo === parentWin.__cyber_expected_combo) {{
                  parentWin.__cyber_combo_ok = true;
                }}

                if (isBlockedCombo(combo)) {{
                  e.preventDefault();
                  e.stopPropagation();
                  if (e.stopImmediatePropagation) e.stopImmediatePropagation();
                  return false;
                }}
              }};

              doc.addEventListener("keydown", parentWin.__cyber_guard_handler, true);
            }}

            if (parentWin.__cyber_expected_combo !== expectedCombo) {{
              parentWin.__cyber_expected_combo = expectedCombo;
              parentWin.__cyber_combo_ok = false;
            }}
            parentWin.__cyber_require_combo = requireCombo;

            if (!parentWin.__cyber_click_guard_bound) {{
              parentWin.__cyber_click_guard_bound = true;
              doc.addEventListener("click", function (e) {{
                if (!parentWin.__cyber_require_combo) return;
                const target = e.target && e.target.closest ? e.target.closest("button") : null;
                if (!target) return;
                const label = (target.innerText || "").trim();
                const isValidateBtn =
                  label.startsWith("Apertei ") ||
                  label.startsWith("Validar comando protegido:") ||
                  label.startsWith("Validar licao");
                if (!isValidateBtn) return;

                if (!parentWin.__cyber_combo_ok) {{
                  e.preventDefault();
                  e.stopPropagation();
                  if (e.stopImmediatePropagation) e.stopImmediatePropagation();
                  alert("Comando nao foi executado. Pressione o atalho da missao antes de validar.");
                  return false;
                }}

                parentWin.__cyber_combo_ok = false;
              }}, true);
            }}

            parentWin.onbeforeunload = function (e) {{
              e.preventDefault();
              e.returnValue = "";
              return "";
            }};
          }} catch (err) {{
            // Silencioso: navegadores variam no controle de atalhos globais.
          }}
        }})();
        </script>
        """,
        height=0,
    )


def render_end() -> None:
    report = build_report()
    pdf_bytes = build_pdf_bytes(report)
    top_player = max(st.session_state.players, key=lambda p: p["xp"]) if st.session_state.players else {"id": "-", "xp": 0}

    st.markdown("<h1 class='title'>Missao Concluida!</h1>", unsafe_allow_html=True)
    st.write(f"{st.session_state.finish_reason} MVP: Jogador {top_player['id']} com {top_player['xp']} XP.")
    st.write(f"Missoes: {report['missions_completed']}/{report['missions_total']} | Tempo total: {report['duration_sec']}s")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_group = re.sub(r"[^a-zA-Z0-9_-]", "-", (st.session_state.turma_grupo or "turma"))
    filename = f"relatorio-{safe_group}-{ts}.pdf"

    st.download_button(
        "Baixar Relatorio PDF",
        data=io.BytesIO(pdf_bytes),
        file_name=filename,
        mime="application/pdf",
        use_container_width=True,
    )

    cfg = github_config_auto()
    if cfg and not st.session_state.report_auto_sent:
        try:
            auto_url = upload_pdf_to_github(pdf_bytes, filename, cfg)
            st.session_state.report_auto_sent = True
            st.session_state.report_auto_status = (
                f"Relatorio enviado automaticamente para o GitHub. {auto_url}"
                if auto_url
                else "Relatorio enviado automaticamente para o GitHub."
            )
        except Exception as exc:
            st.session_state.report_auto_status = f"Envio automatico falhou: {exc}"

    if st.session_state.report_auto_status:
        st.info(st.session_state.report_auto_status)

    if st.button("Enviar PDF para GitHub", type="primary", use_container_width=True):
        if not cfg:
            st.error("GitHub nao configurado no servidor. Defina GITHUB_TOKEN (e opcionalmente owner/repo/branch).")
        else:
            try:
                url = upload_pdf_to_github(pdf_bytes, filename, cfg)
                st.success(f"Relatorio enviado com sucesso. {url}")
            except Exception as exc:
                st.error(f"Falha ao enviar para GitHub: {exc}")

    st.success("Atividade concluida! Liberado treino de digitacao.")
    st.link_button(
        "Ir para treino de digitacao (Ratatype)",
        "https://www.ratatype.com.br/typing-games/race/",
        use_container_width=True,
    )
    components.html(
        """
        <script>
          setTimeout(function () {
            window.open("https://www.ratatype.com.br/typing-games/race/", "_self");
          }, 4000);
        </script>
        """,
        height=0,
    )

    if st.button("Jogar novamente", use_container_width=True):
        st.session_state.stage = "start"
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="CyberCommand: Missao Teclado", page_icon="⌨️", layout="wide", initial_sidebar_state="collapsed")
    css()
    init_state()

    st.warning(
        "O app simula os efeitos dos atalhos em uma janela virtual para treino pedagógico. "
        "Em navegador, atalhos de sistema ainda nao podem ser bloqueados 100%."
    )

    if st.session_state.stage == "start":
        render_start()
    elif st.session_state.stage == "game":
        render_game()
    else:
        render_end()


if __name__ == "__main__":
    main()

