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
        "attach_file_desc": "Anexa à próxima mensagem o texto de um arquivo .md, .markdown ou .txt",
        "remove_file": "Remover anexo",
        "file_truncated": "Arquivo grande: só os primeiros {n} caracteres vão junto",
        "attached_files_msg": "Arquivo anexado: {names}",
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
        "attach_file_desc": "Attach the text of a .md, .markdown or .txt file to the next message",
        "remove_file": "Remove attachment",
        "file_truncated": "Large file: only the first {n} characters go along",
        "attached_files_msg": "Attached file: {names}",
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
