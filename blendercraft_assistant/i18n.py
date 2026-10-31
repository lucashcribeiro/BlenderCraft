"""Panel texts in Portuguese and English.

Portuguese when Blender's interface locale starts with "pt", English
otherwise. Values may hold str.format fields; t() fills them only when
arguments are given, so a template like "queue_dropped" can be handed on
to code that formats it later.
"""

STRINGS: dict[str, dict[str, str]] = {
    "pt": {
        "assistant": "Assistente",
        "idle": "Pronto",
        "working": "Trabalhando…",
        "error": "Erro",
        "new_chat": "Nova conversa",
        "conversations": "Conversas",
        "no_conversations": "Nenhuma conversa salva",
        "untitled": "(sem título)",
        "open_conversation": "Abrir conversa",
        "delete_conversation": "Apagar conversa",
        "stop": "Parar",
        "send": "Enviar",
        "send_now": "Enviar agora",
        "message": "Mensagem…",
        "queued": "{n} na fila",
        "you": "Você",
        "claude": "Claude",
        "attach_view": "Anexar vista",
        "attach_selection": "Anexar seleção",
        "attach_failed": "Não foi possível anexar a vista: {err}",
        "mode": "Permissão",
        "mode_auto": "Automático",
        "mode_auto_desc": "O Claude usa todas as ferramentas sem perguntar",
        "mode_ask": "Perguntar antes de alterar",
        "mode_ask_desc": "Cada ferramenta que altera a cena ou grava arquivos pede aprovação",
        "mode_readonly": "Só leitura",
        "mode_readonly_desc": "Só as ferramentas de inspeção ficam liberadas",
        "model": "Modelo",
        "model_default": "Padrão do Claude Code",
        "changes": "Mudanças: {n}, um passo de desfazer cada",
        "undo_ai": "Desfazer mudanças da IA",
        "undo_ai_done": "{n} passos da IA desfeitos",
        "undo_ai_blocked": (
            "A cena foi editada à mão depois do último pedido. "
            "Desfaça pelo histórico (Edit › Undo History)."
        ),
        "details": "Detalhes",
        "show_older": "Mostrar anteriores",
        "approve": "Aprovar",
        "deny": "Recusar",
        "approval_request": "O Claude quer usar {tool}",
        "claude_missing": (
            "Claude Code não encontrado. Instale-o ou indique o caminho "
            "nas preferências da extensão."
        ),
        "claude_login": (
            "O Claude Code não está logado. Abra um terminal, rode claude "
            "e faça login."
        ),
        "step_limit": "Parado: limite de chamadas de ferramenta deste pedido atingido.",
        "queue_dropped": "({n} mensagens da fila descartadas)",
        "exit_code": "O Claude Code saiu com o código {code}",
        "send_failed": "Não foi possível falar com o Claude Code: {error}",
        "save_failed": "Não foi possível salvar a conversa: {error}",
        "summary_header": "Resumo da conversa anterior:",
        "unit": "Unidade",
        "scale_warning": (
            "Este arquivo não está em milímetros: 1 unidade do Blender = {mm} mm."
        ),
        "joint": "Articulação",
        "joint_pivot": "Pivô",
        "joint_create": "Criar articulação",
        "joint_create_desc": (
            "Faz a peça ativa girar em torno do cursor 3D, no eixo Z"
        ),
        "joint_no_active": "Selecione a peça que vai girar",
        "joint_angle": "Ângulo",
        "joint_reset": "Voltar à posição",
        "joint_test": "Testar articulação",
        "joint_free": "Gira livre de {min}° a {max}°",
        "joint_hit": "Colide com {obj} em {angle}°",
        "joint_at_rest": "Já colide na posição inicial com {obj}",
        "copy_mcp": "Copiar comando MCP",
        "mcp_copied": "Comando copiado",
        "server_off": "O servidor MCP não está rodando",
        "claude_path": "Caminho do Claude Code",
        "claude_path_desc": "Vazio: procura claude no PATH e nos locais de instalação comuns",
        "external_port": "Porta do servidor MCP (0 = aleatória)",
        "external_port_desc": "Porta fixa para usar o servidor de fora; vale ao religar a extensão",
        "gen3d_title": "Geração 3D por IA",
        "meshy_key": "Chave da API do Meshy",
        "meshy_key_desc": "Chave da API do Meshy (meshy.ai, plano pago). Vazio: usa a variável de ambiente MESHY_API_KEY",
        "tripo_key": "Chave da API do Tripo",
        "tripo_key_desc": "Chave da API do Tripo (tsk_…). Vazio: usa a variável de ambiente TRIPO_API_KEY",
        "gen3d_provider": "Serviço padrão",
        "gen3d_provider_desc": "Serviço usado por generate_3d_model quando o Claude não escolhe um",
        "tripo_region": "Site do Tripo",
        "tripo_region_desc": "Onde a chave do Tripo foi criada: a chave só vale no site dela",
        "tripo_region_ai": "Internacional (tripo3d.ai)",
        "tripo_region_com": "China (tripo3d.com)",
        "gen3d_keys_note": "As chaves ficam nas preferências do Blender, nunca no .blend, e não são enviadas ao Claude.",
        "gen3d_offline": (
            "A geração 3D por IA precisa de internet e o acesso online do Blender está "
            "desligado. Ligue em Preferências › Sistema › Rede › Permitir acesso online "
            "(Allow Online Access) e tente de novo."
        ),
        "gen3d_no_key": (
            "Falta a chave da API do {provider}. Cole a chave em Preferências › "
            "Extensões › BlenderCraft Assistant › Geração 3D por IA (ou defina a "
            "variável de ambiente {env}) e tente de novo."
        ),
        "gen3d_bad_key": (
            "O {provider} recusou a chave da API. Confira a chave em Preferências › "
            "Extensões › BlenderCraft Assistant (no Tripo, confira também o site da chave)."
        ),
        "gen3d_no_credits": (
            "O {provider} recusou por falta de créditos ou de plano: a API exige um "
            "plano pago com créditos disponíveis."
        ),
        "gen3d_rate_limit": "O {provider} está limitando os pedidos; espere um pouco e tente de novo.",
        "gen3d_key_hint": "Geração 3D por IA: sem chave. Configure em Preferências › Extensões › BlenderCraft Assistant.",
        "gen3d_running": "Gerando modelo 3D ({provider}): {stage} {percent}",
        "gen3d_cancel": "Cancelar geração",
        "menu_3mf": "3D Manufacturing Format (.3mf)",
        "import_3mf": "Importar 3MF",
        "export_3mf": "Exportar 3MF",
        "export_3mf_desc": "Exporta em milímetros os objetos selecionados (ou todos os visíveis, se nada estiver selecionado)",
        "imported_3mf": "{n} objetos importados",
        "exported_3mf": "{n} objetos gravados em {file}",
        "export_skipped": "Ignorados (sem malha ou sem faces): {names}",
        "no_mesh_to_export": "Nenhuma malha com faces para exportar",
        "provider": "Claude Code",
        "placeholder": "Peça ao agente para mudar o projeto… (Enter: enviar)",
        "placeholder_working": "Deixe na fila para quando o agente terminar (Ctrl+Enter: enviar agora)",
        "send_key": "Enviar (Enter)",
        "send_key_now": "Enviar agora (Ctrl/Cmd+Enter)",
        "queue": "Pôr na fila",
        "attach_selection_n": "Anexar seleção ({n})",
        "pick_context": "Escolher contexto",
        "pick_context_desc": "Anexa à próxima mensagem os objetos selecionados na vista 3D ou no Outliner",
        "pick_context_done": "{n} objetos selecionados vão junto com a próxima mensagem",
        "pick_context_none": "Selecione objetos na vista 3D ou no Outliner primeiro",
        "settings": "Ajustes",
        "tool_ok": "feito",
        "tool_running": "rodando",
        "tool_error": "erro",
        "tool_interrupted": "interrompido",
        "paste_draft": "Colar",
        "paste_draft_desc": "Cola um texto de várias linhas (Markdown, tabelas…) da área de transferência na mensagem",
        "clipboard_empty": "A área de transferência está vazia",
        "edit_draft": "Editor",
        "edit_draft_desc": "Escreve a mensagem num editor de várias linhas, numa janela própria",
        "draft_in_text_editor": "Rascunho aberto no Editor de Texto: {name}",
        "clear_draft": "Descartar rascunho",
        "draft_lines": "{n} linhas",
        "draft_name": "BlenderCraft — mensagem",
        "attach_file": "Anexar arquivo",
        "attach_file_desc": "Anexa à próxima mensagem o texto de um arquivo .md, .markdown ou .txt, ou uma imagem (.png, .jpg, .webp), que o Claude vê e pode usar para gerar um modelo 3D",
        "remove_file": "Remover anexo",
        "file_truncated": "Arquivo grande: só os primeiros {n} caracteres vão junto",
        "attached_files_msg": "Arquivo anexado: {names}",
        "open_model": "Abrir modelo… (STL, 3MF, OBJ, PLY, GLB)",
        "open_model_desc": "Importa STL, 3MF, OBJ, PLY ou glTF na cena atual, no tamanho certo (STL em mm)",
        "opened_models": "{n} objetos abertos",
        "toggle_assistant": "Assistente",
        "toggle_assistant_desc": "Mostra ou esconde o Assistente na lateral da vista 3D",
        "no_3d_view": "Nenhuma vista 3D nesta tela",
    },
    "en": {
        "assistant": "Assistant",
        "idle": "Ready",
        "working": "Working…",
        "error": "Error",
        "new_chat": "New chat",
        "conversations": "Conversations",
        "no_conversations": "No saved conversations",
        "untitled": "(untitled)",
        "open_conversation": "Open conversation",
        "delete_conversation": "Delete conversation",
        "stop": "Stop",
        "send": "Send",
        "send_now": "Send now",
        "message": "Message…",
        "queued": "{n} queued",
        "you": "You",
        "claude": "Claude",
        "attach_view": "Attach view",
        "attach_selection": "Attach selection",
        "attach_failed": "Could not attach the view: {err}",
        "mode": "Permission",
        "mode_auto": "Automatic",
        "mode_auto_desc": "Claude uses every tool without asking",
        "mode_ask": "Ask before changing",
        "mode_ask_desc": "Each tool that changes the scene or writes files asks for approval",
        "mode_readonly": "Read only",
        "mode_readonly_desc": "Only inspection tools are allowed",
        "model": "Model",
        "model_default": "Claude Code default",
        "changes": "Changes: {n}, one undo step each",
        "undo_ai": "Undo AI changes",
        "undo_ai_done": "Undid {n} AI steps",
        "undo_ai_blocked": (
            "The scene was edited by hand after the last request. "
            "Undo from the history (Edit › Undo History)."
        ),
        "details": "Details",
        "show_older": "Show older",
        "approve": "Approve",
        "deny": "Deny",
        "approval_request": "Claude wants to use {tool}",
        "claude_missing": (
            "Claude Code not found. Install it or set its path in the "
            "extension preferences."
        ),
        "claude_login": (
            "Claude Code is not logged in. Open a terminal, run claude and "
            "log in."
        ),
        "step_limit": "Stopped: tool call limit reached for this request.",
        "queue_dropped": "({n} queued messages discarded)",
        "exit_code": "Claude Code exited with code {code}",
        "send_failed": "Could not talk to Claude Code: {error}",
        "save_failed": "Could not save the conversation: {error}",
        "summary_header": "Summary of the previous conversation:",
        "unit": "Unit",
        "scale_warning": (
            "This file is not in millimeters: 1 Blender unit = {mm} mm."
        ),
        "joint": "Joint",
        "joint_pivot": "Pivot",
        "joint_create": "Create joint",
        "joint_create_desc": (
            "Make the active part turn about the 3D cursor, on the Z axis"
        ),
        "joint_no_active": "Select the part that will turn",
        "joint_angle": "Angle",
        "joint_reset": "Back to rest",
        "joint_test": "Test joint",
        "joint_free": "Turns freely from {min}° to {max}°",
        "joint_hit": "Hits {obj} at {angle}°",
        "joint_at_rest": "Already hits {obj} at rest",
        "copy_mcp": "Copy MCP command",
        "mcp_copied": "Command copied",
        "server_off": "The MCP server is not running",
        "claude_path": "Claude Code path",
        "claude_path_desc": "Empty: look for claude on PATH and in the usual install locations",
        "external_port": "MCP server port (0 = random)",
        "external_port_desc": "Fixed port to use the server from outside; applies when the extension is re-enabled",
        "gen3d_title": "AI 3D generation",
        "meshy_key": "Meshy API key",
        "meshy_key_desc": "Meshy API key (meshy.ai, paid plan). Empty: use the MESHY_API_KEY environment variable",
        "tripo_key": "Tripo API key",
        "tripo_key_desc": "Tripo API key (tsk_…). Empty: use the TRIPO_API_KEY environment variable",
        "gen3d_provider": "Default service",
        "gen3d_provider_desc": "Service generate_3d_model uses when Claude does not pick one",
        "tripo_region": "Tripo site",
        "tripo_region_desc": "Where the Tripo key was created: a key only works on its own site",
        "tripo_region_ai": "International (tripo3d.ai)",
        "tripo_region_com": "China (tripo3d.com)",
        "gen3d_keys_note": "Keys stay in Blender's preferences, never in the .blend, and are not sent to Claude.",
        "gen3d_offline": (
            "AI 3D generation needs the internet and Blender's online access is off. "
            "Turn on Preferences › System › Network › Allow Online Access and try again."
        ),
        "gen3d_no_key": (
            "The {provider} API key is missing. Paste it in Preferences › Extensions › "
            "BlenderCraft Assistant › AI 3D generation (or set the {env} environment "
            "variable) and try again."
        ),
        "gen3d_bad_key": (
            "{provider} rejected the API key. Check it in Preferences › Extensions › "
            "BlenderCraft Assistant (for Tripo, also check the key's site)."
        ),
        "gen3d_no_credits": (
            "{provider} refused for lack of credits or plan: the API needs a paid "
            "plan with credits left."
        ),
        "gen3d_rate_limit": "{provider} is rate limiting requests; wait a moment and try again.",
        "gen3d_key_hint": "AI 3D generation: no key. Set one in Preferences › Extensions › BlenderCraft Assistant.",
        "gen3d_running": "Generating 3D model ({provider}): {stage} {percent}",
        "gen3d_cancel": "Cancel generation",
        "menu_3mf": "3D Manufacturing Format (.3mf)",
        "import_3mf": "Import 3MF",
        "export_3mf": "Export 3MF",
        "export_3mf_desc": "Export the selected objects (or every visible one when nothing is selected) in millimeters",
        "imported_3mf": "Imported {n} objects",
        "exported_3mf": "Wrote {n} objects to {file}",
        "export_skipped": "Skipped (not a mesh or no faces): {names}",
        "no_mesh_to_export": "No mesh with faces to export",
        "provider": "Claude Code",
        "placeholder": "Ask the agent to change the design… (Enter: send)",
        "placeholder_working": "Queue a message for when the agent is done (Ctrl+Enter: send now)",
        "send_key": "Send (Enter)",
        "send_key_now": "Send now (Ctrl/Cmd+Enter)",
        "queue": "Queue",
        "attach_selection_n": "Attach selection ({n})",
        "pick_context": "Pick context",
        "pick_context_desc": "Attach the objects selected in the 3D View or the Outliner to the next message",
        "pick_context_done": "{n} selected objects go with the next message",
        "pick_context_none": "Select objects in the 3D View or the Outliner first",
        "settings": "Settings",
        "tool_ok": "done",
        "tool_running": "running",
        "tool_error": "error",
        "tool_interrupted": "interrupted",
        "paste_draft": "Paste",
        "paste_draft_desc": "Paste a multi-line text (Markdown, tables…) from the clipboard into the message",
        "clipboard_empty": "The clipboard is empty",
        "edit_draft": "Editor",
        "edit_draft_desc": "Write the message in a multi-line editor, in its own window",
        "draft_in_text_editor": "Draft open in the Text Editor: {name}",
        "clear_draft": "Discard draft",
        "draft_lines": "{n} lines",
        "draft_name": "BlenderCraft — message",
        "attach_file": "Attach file",
        "attach_file_desc": "Attach the text of a .md, .markdown or .txt file, or an image (.png, .jpg, .webp) that Claude sees and can use to generate a 3D model, to the next message",
        "remove_file": "Remove attachment",
        "file_truncated": "Large file: only the first {n} characters go along",
        "attached_files_msg": "Attached file: {names}",
        "open_model": "Open model… (STL, 3MF, OBJ, PLY, GLB)",
        "open_model_desc": "Import STL, 3MF, OBJ, PLY or glTF into the current scene at the right size (STL in mm)",
        "opened_models": "Opened {n} objects",
        "toggle_assistant": "Assistant",
        "toggle_assistant_desc": "Show or hide the Assistant in the 3D View sidebar",
        "no_3d_view": "No 3D View on this screen",
    },
}


def _locale() -> str:
    import bpy

    return bpy.app.translations.locale or ""


def lang() -> str:
    return "pt" if _locale().startswith("pt") else "en"


def t(key: str, **fmt) -> str:
    """Text for key in the interface language (English, then the key itself,
    as fallbacks)."""
    text = STRINGS[lang()].get(key) or STRINGS["en"].get(key) or key
    return text.format(**fmt) if fmt else text
