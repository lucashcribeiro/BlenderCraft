# BlenderCraft — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Objetivo:** Blender 5.2 com template "BlenderCraft" (sem animação à vista, 1 BU = 1 mm) e uma extensão que embute o Claude Code num painel lateral, operando a cena por um servidor MCP interno.

**Arquitetura:**
- Uma extensão Blender (`blendercraft_assistant`) com quatro partes:
  - ferramentas `bpy` registradas num catálogo;
  - um servidor MCP HTTP local que expõe esse catálogo;
  - uma ponte que roda `claude -p` em stream-json como subprocesso;
  - um painel nativo.
- Um template de aplicação configura a tela e as unidades.
- Tudo que toca `bpy` roda no thread principal, por meio de uma fila.

**Tech Stack:** Blender 5.2.2 LTS (Python 3.13 embutido), só a biblioteca padrão do Python, Claude Code CLI ≥ 2.1.310, `unittest` rodando dentro do Blender headless.

**Spec:** `docs/superpowers/specs/2026-10-30-blendercraft-design.md`

## Global Constraints

- Blender alvo: `/Applications/Blender.app/Contents/MacOS/Blender`, versão 5.2.2 LTS; `blender_version_min = "5.2.0"`.
- Só macOS na v1.
- Sem dependências externas: apenas a biblioteca padrão do Python, sem `pip`.
- Licença da extensão: `GPL-3.0-or-later`; id `blendercraft_assistant`; versão `0.1.0`.
- Dentro do pacote, só imports relativos: em runtime o módulo se chama `bl_ext.user_default.blendercraft_assistant`, e nos testes, `blendercraft_assistant`.
- Módulos sem `bpy` (protocolo MCP, ponte Claude, conversas, quebra de texto, catálogo base) não importam `bpy` no topo.
- Internamente 1 BU = 1 mm (`scale_length = 0.001`); STL/3MF sempre saem em mm.
- Unidades exibíveis: mm, cm, m, polegadas e pés.
- Textos de UI em português e inglês: português se o locale do Blender começar com `pt`, inglês nos demais casos.
- Chaves e tokens nunca são gravados no `.blend`.
- O servidor MCP escuta só em `127.0.0.1` e exige `Authorization: Bearer <token>`, com token aleatório por sessão (`secrets.token_urlsafe(32)`).
- Ferramentas embutidas do Claude Code limitadas à `Skill` (`--tools Skill`), necessária para usar as skills do plugin, e `--strict-mcp-config`.
- Limite de 40 chamadas de ferramenta por pedido; `undo_steps = 256`.
- Conversas em `~/Library/Application Support/BlenderCraft/conversations/`.
- Comando de teste (referido abaixo como `RUN_TESTS`):
  `/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 --python tests/run.py -- [-k PADRÃO]`

## Review Focus

1. **Blender aberto pelo Finder/Dock tem PATH mínimo.** `claude` não é encontrado pelo `PATH`; `find_claude` deve achar `~/.local/bin/claude` e os caminhos do Homebrew. Sem `claude`, o painel mostra instruções em vez de erro mudo. (Testes na Task 11.)
2. **Arquivo `.blend` de terceiros com outra escala** (`scale_length = 1.0` em metros, ou sistema `NONE`): um cubo de 20 mm deve sair com 20 no STL e no 3MF. (Testes na Task 7.)
3. **Sessão do Claude Code que não pode mais ser retomada** (apagada ou expirada): a conversa continua numa sessão nova, com um resumo, sem travar. (Teste na Task 12.)
4. **Requisição ao MCP vinda de outro programa ou do navegador** (sem token, ou com `Host` diferente de `127.0.0.1`/`localhost`): deve ser recusada com 401/403. (Testes na Task 10.)
5. **Fechar o Blender, abrir "Nova conversa" ou clicar em Parar com o Claude no meio**: o processo é encerrado sem deixar órfãos, a conversa é salva e as ferramentas em andamento ficam como `interrupted`. (Teste na Task 12.)

---

## Estrutura de arquivos

```
blendercraft_assistant/            extensão
  blender_manifest.toml
  __init__.py                      register/unregister (Task 13)
  runtime.py                       singleton: servidor, sessão, store (Task 13)
  i18n.py                          t(key) pt/en (Task 13)
  units.py                         unidades (Task 2)
  mainthread.py                    fila para o thread principal (Task 3)
  undo.py                          passos nomeados + ChangeTracker (Task 3)
  tools/
    base.py                        Tool, ToolResult, catálogo, sem bpy (Task 4)
    dispatch.py                    call_tool com undo/erros (Task 4)
    introspect.py                  list_operators, describe_operator (Task 4)
    inspect.py                     inspect_scene, inspect_object, measure (Task 4)
    python_exec.py                 run_python, undo, redo, save (Task 5)
    topology.py                    mesh_topology, print_check (Task 6)
    files.py                       import_file, export_file (Task 7)
    view.py                        screenshot (Task 8)
    joints.py                      pivôs e joint_test (Task 9)
  io_3mf.py                        export_3mf, import_3mf (Task 7)
  mcp/protocol.py                  McpHandler JSON-RPC, sem bpy (Task 10)
  mcp/server.py                    McpServer HTTP, sem bpy (Task 10)
  claude/stream.py                 parse_line → eventos (Task 11)
  claude/process.py                find_claude, build_args, ClaudeProcess (Task 11)
  claude/session.py                AgentSession (Task 12)
  claude/system_prompt.py          build_system_prompt (Task 14)
  conversations.py                 ConversationStore (Task 12)
  ui/text_wrap.py                  wrap, strip_markdown (Task 13)
  ui/props.py, ui/operators.py, ui/panel.py, ui/prefs.py   (Task 13)
  plugin/                          plugin do Claude Code com skills (Task 14)
app_template/blendercraft/__init__.py   (Task 15)
scripts/install.sh, scripts/BlenderCraft.command, scripts/record_fixture.py
tests/run.py, tests/test_*.py, tests/fixtures/*.jsonl, tests/fake_claude.py
```

---

### Task 1: Esqueleto, manifesto e executor de testes

**Files:**
- Create: `blendercraft_assistant/blender_manifest.toml`, `blendercraft_assistant/__init__.py` (com `register()`/`unregister()` vazios por enquanto), `tests/run.py`, `tests/test_smoke.py`, `.gitignore` (`__pycache__/`, `*.blend1`, `/dist/`)

**Interfaces:**
- Produz: `tests/run.py`. Põe a raiz do repo no `sys.path`, descobre `tests/test_*.py`, aceita `-k PADRÃO` depois de `--` (via `TestLoader.testNamePatterns`) e lança `SystemExit(1)` se houver falha.

- [ ] **Step 1:** `git init` na raiz do projeto.
- [ ] **Step 2: Escrever o teste que falha.** `tests/test_smoke.py`:

```python
class SmokeTest(unittest.TestCase):
    def test_manifest(self):
        data = tomllib.loads((ROOT / "blendercraft_assistant/blender_manifest.toml").read_text())
        self.assertEqual(data["id"], "blendercraft_assistant")
        self.assertEqual(data["version"], "0.1.0")
        self.assertEqual(data["blender_version_min"], "5.2.0")
        self.assertEqual(data["license"], ["SPDX:GPL-3.0-or-later"])
        self.assertEqual(data["type"], "add-on")

    def test_package_imports(self):
        import blendercraft_assistant
        self.assertTrue(callable(blendercraft_assistant.register))
```

- [ ] **Step 3:** `RUN_TESTS` → falha (arquivos inexistentes).
- [ ] **Step 4:** Criar manifesto (`schema_version = "1.0.0"`, `name = "BlenderCraft Assistant"`, `tagline`, `maintainer`) e o `__init__.py` mínimo.
- [ ] **Step 5:** `RUN_TESTS` → 2 testes OK.
- [ ] **Step 6:** Commit `chore: esqueleto da extensão e executor de testes`.

---

### Task 2: Unidades

**Files:** Create `blendercraft_assistant/units.py`; Test `tests/test_units.py`

**Interfaces:**
- Produz:
  - `DISPLAY_UNITS: dict[str, tuple[str, str]]` = `{"MM": ("METRIC","MILLIMETERS"), "CM": ("METRIC","CENTIMETERS"), "M": ("METRIC","METERS"), "IN": ("IMPERIAL","INCHES"), "FT": ("IMPERIAL","FEET")}`
  - `apply_internal_mm(scene) -> None`: `system="METRIC"`, `scale_length=0.001`, `length_unit="MILLIMETERS"`.
  - `set_display_unit(scene, unit: str) -> None`: muda `system` e `length_unit`, **nunca** `scale_length`. Unidade desconhecida → `ValueError`.
  - `get_display_unit(scene) -> str`
  - `mm_per_bu(scene) -> float`: `scale_length * 1000`; se `system == "NONE"`, devolve `1.0`.
  - `format_length(value_bu: float, scene) -> str`: usa `bpy.utils.units.to_string` com precisão 3.

- [ ] **Step 1: Testes.**
  - `test_internal_mm`: após `apply_internal_mm`, `mm_per_bu == 1.0` e `get_display_unit == "MM"`.
  - `test_display_inches_keeps_scale`: após `set_display_unit(scene, "IN")`, `scale_length == 0.001` e `"0.787" in format_length(20, scene)`.
  - `test_meters_scene`: com `scale_length = 1.0` e `system = "METRIC"`, `mm_per_bu == 1000.0`.
  - `test_none_system`: com `system = "NONE"`, `mm_per_bu == 1.0`.
  - `test_unknown_unit`: `set_display_unit(scene, "XX")` lança `ValueError`.
- [ ] **Step 2:** `RUN_TESTS -k test_units` → falha.
- [ ] **Step 3:** Implementar `units.py`.
- [ ] **Step 4:** `RUN_TESTS -k test_units` → OK.
- [ ] **Step 5:** Commit `feat: unidades com mm interno e unidade exibida trocável`.

---

### Task 3: Thread principal e desfazer

**Files:** Create `blendercraft_assistant/mainthread.py`, `blendercraft_assistant/undo.py`; Test `tests/test_mainthread.py`, `tests/test_undo.py`

**Interfaces:**
- Produz (`mainthread`):
  - `run_on_main(fn: Callable[[], T], timeout: float = 120.0) -> T`: se já estiver no thread principal, chama `fn` direto. Se não, enfileira e espera o resultado; repassa a exceção de `fn`; lança `TimeoutError` no tempo limite.
  - `post(fn: Callable[[], None]) -> None`: enfileira sem esperar.
  - `pump(max_items: int = 100) -> int`: executa até `max_items` itens da fila e devolve quantos executou.
  - `install_timer() -> None` / `uninstall_timer() -> None`: timer persistente de 0.05 s que chama `pump()`. Em modo background os timers não disparam, por isso os testes chamam `pump()` à mão.
- Produz (`undo`):
  - `push_step(label: str) -> None` → `bpy.ops.ed.undo_push(message=f"IA: {label}")`
  - `class ChangeTracker`:
    - `begin_turn()`, `record_ai_step()`, `ai_scope()` (context manager);
    - `on_depsgraph_update(scene, depsgraph)`: marca edição manual só se estiver fora de `ai_scope` e algum update tiver `is_updated_geometry` ou `is_updated_transform`;
    - `can_undo_last_turn() -> bool`: há passos da IA no turno e nenhuma edição manual desde `begin_turn`;
    - `undo_last_turn() -> int`: chama `bpy.ops.ed.undo()` N vezes e devolve N; se `can_undo_last_turn()` for falso, lança `RuntimeError`.

- [ ] **Step 1: Testes.**
  - `test_run_on_main_from_worker`: uma thread chama `run_on_main(lambda: 42)`; o teste chama `pump()` até a thread terminar; resultado `42`.
  - `test_exception_propagates`: `run_on_main` de `fn` que lança `ValueError` → a thread recebe `ValueError`.
  - `test_timeout`: `run_on_main(fn, timeout=0.1)` sem `pump()` → `TimeoutError`.
  - `test_undo_last_turn`:
    - `begin_turn`;
    - dentro de `ai_scope`, adicionar um cubo e chamar `push_step("cubo")` e `record_ai_step()`;
    - `undo_last_turn() == 1` e o cubo deixa de existir.
  - `test_manual_edit_blocks`: `begin_turn` + passo da IA, depois mover um objeto fora de `ai_scope` e chamar `on_depsgraph_update` com o depsgraph atual → `can_undo_last_turn()` é falso.
- [ ] **Step 2:** `RUN_TESTS -k "test_mainthread or test_undo"` → falha.
- [ ] **Step 3:** Implementar. A fila é uma `queue.SimpleQueue` de `(fn, Future | None)`; o thread principal é identificado com `threading.main_thread()`.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: fila do thread principal e rastreio de mudanças da IA`.

---

### Task 4: Catálogo de ferramentas e ferramentas de inspeção

**Files:** Create `tools/base.py`, `tools/dispatch.py`, `tools/introspect.py`, `tools/inspect.py`, `tools/__init__.py` (importa os módulos de ferramentas para registrá-las); Test `tests/test_tools_inspect.py`

**Interfaces:**
- Produz (`tools/base.py`, sem bpy):

```python
@dataclass
class ToolResult:
    text: str
    images: list[bytes] = field(default_factory=list)   # PNG
    is_error: bool = False

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], ToolResult]
    kind: Literal["read", "scene", "file"]   # read: só lê; scene: altera a cena; file: grava disco
    push_undo: bool = True                    # só vale para kind == "scene"

REGISTRY: dict[str, Tool]
def register_tool(tool: Tool) -> None              # nome duplicado → ValueError
def tools_for_mode(mode: str) -> list[Tool]        # "AUTO"|"ASK"|"READONLY"; READONLY só kind == "read"
def truncate(text: str, limit: int = 8000) -> str  # acrescenta "\n…[truncado N caracteres]"
```

- Produz (`tools/dispatch.py`): `call_tool(name: str, args: dict, tracker: ChangeTracker | None = None) -> ToolResult`.
  - Roda o handler dentro de `tracker.ai_scope()`.
  - Se `kind == "scene"` e `push_undo`, chama `push_step(args.get("description") or name)` e `tracker.record_ai_step()`.
  - Exceção vira `ToolResult(text=traceback, is_error=True)`; nome desconhecido também vira `is_error`.
  - Toda saída passa por `truncate`.
- Ferramentas (os nomes são o contrato com o Claude):

| nome | kind | entrada → saída (texto JSON) |
|---|---|---|
| `list_operators` | read | `{query: str, limit?: int=30}` → `[{id, label, description}]` buscando em `bpy.ops` por substring em id/label/descrição |
| `describe_operator` | read | `{id: "mesh.bevel"}` → `{id, description, params:[{name, type, default, min?, max?, items?}]}` via `get_rna_type()` |
| `inspect_scene` | read | `{}` → `{unit, objects:[{name, type, parent, location, rotation_deg, scale, dimensions, materials, modifiers, visible, selected}], active}`; comprimentos já na unidade exibida (via `format_length`) |
| `inspect_object` | read | `{name}` → mesmo formato + `vertices`, `faces`, `bbox_min`, `bbox_max` |
| `measure` | read | `{a: str, b?: str}`: só `a` → `{dimensions, volume, area}`; com `b` → `{distance_between_origins, min_gap}`; `min_gap` usa a BVH das malhas avaliadas |

- [ ] **Step 1: Testes.**
  - `test_register_duplicate`.
  - `test_readonly_filter`: com uma ferramenta de cada `kind`, `tools_for_mode("READONLY")` devolve só a `read`.
  - `test_truncate`.
  - `test_unknown_tool_is_error`.
  - `test_list_operators_finds_bevel`: `query="bevel"` traz `mesh.bevel`.
  - `test_describe_bevel_has_width`.
  - `test_inspect_scene_units`: cena em mm com cubo de 20 mm → a dimensão contém `"20"` e `"mm"`.
  - `test_measure_gap`: dois cubos de 20 mm separados por 5 mm → `min_gap` ≈ 5 (±0.01).
  - `test_exception_becomes_error`: ferramenta que lança → `is_error` e o texto contém o nome da exceção.
- [ ] **Step 2:** `RUN_TESTS -k test_tools_inspect` → falha.
- [ ] **Step 3:** Implementar os módulos.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: catálogo de ferramentas e inspeção da cena`.

---

### Task 5: run_python, undo, redo e save

**Files:** Create `tools/python_exec.py`; Test `tests/test_tools_python.py`

**Interfaces:**

| nome | kind | push_undo | comportamento |
|---|---|---|---|
| `run_python` | scene | sim | `{code: str, description: str}`. `exec` num namespace com `bpy`, `bmesh`, `mathutils`, `Vector`, `Matrix`, `math`, `__name__="__bc__"`. Captura o stdout (`redirect_stdout`). Se a última instrução for uma expressão, devolve também o `repr` dela (separar com `ast`). Sem restrição de acesso (spec). |
| `undo` | scene | não | `{steps?: int=1}` → `bpy.ops.ed.undo()` × steps |
| `redo` | scene | não | `{steps?: int=1}` |
| `save` | file | — | `{path?: str}`. Sem `path` e com arquivo sem nome → erro pedindo caminho. |

- [ ] **Step 1: Testes.**
  - `test_stdout_and_expression`: `code="print('a')\n1+1"` → o texto contém `a` e `2`.
  - `test_error_traceback`: `1/0` → `is_error` e `ZeroDivisionError`.
  - `test_creates_and_undo`:
    - pelo `call_tool`, `run_python` cria um cubo (`description="cubo"`);
    - depois `call_tool("undo", {})`;
    - o cubo some.
  - `test_save_requires_path`.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: run_python, undo, redo e save`.

---

### Task 6: Topologia e checagem de impressão

**Files:** Create `tools/topology.py`; Test `tests/test_topology.py`

**Interfaces:**
- `mesh_topology(obj_name: str) -> dict` com:
  - `components` (componentes conectados por arestas);
  - `manifold`, `boundary_edges`, `non_manifold_edges`;
  - `inverted_normals` (volume com sinal < 0);
  - `vertices`, `faces`, `volume_mm3`.
  Tudo sobre a malha avaliada em coordenadas de mundo, convertida para mm com `mm_per_bu`.
- `print_check(obj_name: str, min_wall_mm: float = 0.8, overhang_deg: float = 45.0) -> dict` com:
  - `thin_faces`, `min_wall_mm`: raio do centro de cada face na direção `-normal`, e a distância até a próxima face atingida (BVH);
  - `overhang_faces`, `overhang_area_mm2`: faces com `normal.z < -cos(overhang_deg)`, excluindo as faces cuja altura mínima está a até 0.01 mm do menor Z do objeto;
  - `below_bed_mm`: `max(0, -menor Z)`.
- Ferramentas `mesh_topology` (read, `{name}`) e `print_check` (read, `{name, min_wall_mm?, overhang_deg?}`).

- [ ] **Step 1: Testes** (cena em mm).
  - Cubo de 20 mm + esfera solta, unidos num objeto → `components == 2`.
  - Cubo com uma face apagada → `manifold is False` e `boundary_edges == 4`.
  - Cubo com normais invertidas → `inverted_normals is True`.
  - Placa 20×20×0.4 → `thin_faces > 0` e `abs(min_wall_mm - 0.4) < 0.01`.
  - Cubo de 20 mm com o centro em z=10 (apoiado na mesa) → `overhang_faces == 0` e `below_bed_mm == 0`.
  - Cubo centrado na origem → `below_bed_mm == 10`.
  - Cubo apoiado na mesa com uma laje 20×20×2 saindo de lado a 10 mm de altura → `overhang_area_mm2 > 0`.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar com `bmesh` + `mathutils.bvhtree.BVHTree`.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: topologia de malha e checagem de impressão`.

---

### Task 7: 3MF e importar/exportar

**Files:** Create `blendercraft_assistant/io_3mf.py`, `tools/files.py`; Test `tests/test_io.py`

**Interfaces:**
- `export_3mf(path: str, objects: list, mm_per_bu: float) -> None`:
  - malhas avaliadas (modificadores aplicados), trianguladas, com transformação de mundo e coordenadas × `mm_per_bu`;
  - zip com três arquivos:
    - `[Content_Types].xml`: `model` → `application/vnd.ms-package.3dmanufacturing-3dmodel+xml`; `rels` → `application/vnd.openxmlformats-package.relationships+xml`;
    - `_rels/.rels`: `Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"`, `Target="/3D/3dmodel.model"`;
    - `3D/3dmodel.model`: namespace `http://schemas.microsoft.com/3dmanufacturing/core/2015/02`, `unit="millimeter"`, um `<object type="model" name=…>` por objeto, `<basematerials>` com `displaycolor="#RRGGBB"` (cor do primeiro material convertida para sRGB; sem material, `#CCCCCC`) e um `<item>` por objeto no `<build>`.
- `import_3mf(path: str, mm_per_bu: float) -> list`: um objeto Blender por `<object>` que tenha malha, com nome e cor (cria material); aplica a transformação do `<item>` e respeita o `unit` do arquivo.
- Ferramentas:
  - `import_file` (scene, `{path}`, extensões `.stl .obj .ply .glb .gltf .3mf`);
  - `export_file` (file, `{path, objects?: [str], format?: str}`): o formato vem da extensão do caminho; sem `objects`, exporta a seleção.
    - STL, OBJ e PLY usam `bpy.ops.wm.stl_export` / `obj_export` / `ply_export` com `export_selected_objects=True`, `global_scale=mm_per_bu(scene)` e `apply_modifiers=True`; seleção e objeto ativo são restaurados depois;
    - glTF usa `bpy.ops.export_scene.gltf` sem escala;
    - 3MF usa `export_3mf`.

- [ ] **Step 1: Testes.**
  - `test_3mf_structure`:
    - dois cubos de 20 mm, um com material vermelho e outro azul;
    - abrir o zip → `unit == "millimeter"`, 2 objetos, 12 triângulos cada, cores `#FF0000` e `#0000FF`.
  - `test_3mf_roundtrip`: importar o arquivo → 2 objetos com `dimensions` ≈ 20 mm.
  - `test_stl_mm_any_display` (Review Focus 2):
    - cena em mm, unidade exibida `IN`, cubo de 20 mm → STL binário com bbox de lado 20.0 (ler com `struct`).
  - `test_stl_meters_scene` (Review Focus 2): `scale_length = 1.0`, cubo de 0.02 BU → STL com lado 20.0.
  - `test_3mf_meters_scene` (Review Focus 2): mesma cena → vértices do 3MF com lado 20.0.
  - `test_export_restores_selection`.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar com `zipfile` + `xml.etree.ElementTree`.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: exportação/importação 3MF e exportação em mm`.

---

### Task 8: Screenshot

**Files:** Create `tools/view.py`; Test `tests/test_view.py`

**Interfaces:**
- `screenshot(view: str = "viewport", size: int = 768) -> bytes` (PNG).
  - Vistas: `viewport`, `front`, `back`, `left`, `right`, `top`, `bottom`, `iso`.
  - `viewport`: com interface disponível, usa `bpy.ops.screen.screenshot_area` na maior 3D View; em background, cai para `iso`.
  - Vistas fixas:
    - câmera temporária ortográfica enquadrando a bbox dos objetos de malha visíveis (margem de 10%);
    - motor `BLENDER_WORKBENCH` com iluminação `STUDIO`, resolução `size × size`;
    - renderiza num arquivo temporário e lê os bytes;
    - restaura `scene.camera`, o motor e a resolução, e remove a câmera.
- Ferramenta `screenshot` (read, `push_undo` não se aplica): `{view?: str, size?: int}` → `ToolResult(text="vista: iso", images=[png])`.

- [ ] **Step 1: Testes.**
  - `test_iso_png`: os bytes começam com `b"\x89PNG"`, e a largura no IHDR é 768.
  - `test_restores_scene`: a câmera e o motor ficam como antes, e nenhum objeto novo sobra.
  - `test_unknown_view_error`.
  - Se o render falhar por falta de contexto de GPU no `-b`, marcar `skipTest` com a mensagem, e cobrir na checagem manual da Task 15.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: screenshot da viewport e de ângulos fixos`.

---

### Task 9: Articulação

**Files:** Create `tools/joints.py`; Test `tests/test_joints.py`

**Interfaces:**
- `create_pivot(part: str, point: tuple[float, float, float], axis: str) -> str`:
  - cria um EMPTY `BC_Pivo_<part>` em `point` (BU);
  - faz `part` filha dele, mantendo a transformação de mundo;
  - guarda `["bc_axis"]` (`"X"|"Y"|"Z"`) e `["bc_rest"]` (rotação inicial);
  - devolve o nome do empty.
- `set_angle(pivot: str, degrees: float) -> None`: só rotação, nunca keyframe.
- `reset(pivot: str) -> None`
- `joint_test(pivot: str, against: list[str] | None = None, start: float = -180.0, end: float = 180.0, step: float = 5.0) -> dict` →
  `{"free_min": float, "free_max": float, "colliding_at_rest": bool, "collisions": [{"angle": float, "with": str}]}`.
  - Varre de 0 a `end` e de 0 a `start`, parando em cada sentido na primeira colisão.
  - Colisão = sobreposição de BVH (`BVHTree.FromObject(obj, depsgraph)` + `overlap`) entre `part` e os objetos de `against` (padrão: todas as malhas visíveis que não são `part` nem filhas dele).
  - `free_*` é o último ângulo livre.
  - Restaura o ângulo de antes do teste.
- Ferramentas: `joint_create` (scene, `{part, point, axis, description}`), `joint_set_angle` (scene, `push_undo=True`), `joint_test` (read; o estado é restaurado).

- [ ] **Step 1: Testes** (cena em mm).
  - Barra 10×2×2 de x=0 a 10; pivô na origem com eixo Z; obstáculo de 2×2×2 centrado em (0, 6, 0).
  - `test_sweep`: `joint_test` → `60 <= free_max <= 70`, `free_min == -180.0` e `colliding_at_rest is False`.
  - `test_restores_angle`.
  - `test_no_keyframes`: depois de `set_angle`, `obj.animation_data is None` (tanto o pivô quanto a peça).
  - `test_rest_collision`: obstáculo encostado na barra → `colliding_at_rest is True`.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: pivôs e teste de articulação sem animação`.

---

### Task 10: Servidor MCP

**Files:** Create `mcp/__init__.py`, `mcp/protocol.py`, `mcp/server.py`; Test `tests/test_mcp.py`

**Interfaces:**
- `protocol.py` (sem bpy):

```python
PROTOCOL_VERSION = "2025-06-18"
APPROVAL_TOOL = "approve_action"
class McpHandler:
    def __init__(self, list_tools: Callable[[], list[Tool]],
                 call: Callable[[str, dict], ToolResult],
                 approve: Callable[[str, dict], tuple[bool, str]] | None = None,
                 name: str = "blendercraft", version: str = "0.1.0"): ...
    def handle(self, message: dict) -> dict | None   # None para notificações
```

  - Métodos aceitos:
    - `initialize`: devolve `protocolVersion`, `capabilities: {"tools": {}}` e `serverInfo`;
    - `notifications/initialized` → `None`;
    - `ping` → `{}`;
    - `tools/list`: `[{name, description, inputSchema}]`, mais `approve_action` se `approve` existir;
    - `tools/call`: `content` = `[{"type":"text","text":…}]` + `[{"type":"image","data":b64,"mimeType":"image/png"}]` por imagem, e `isError`.
  - Método desconhecido → erro `-32601`.
  - `approve_action`:
    - entrada `{tool_name: str, input: object}`;
    - chama `approve(tool_name, input)`;
    - responde com o texto JSON `{"behavior":"allow","updatedInput": input}` ou `{"behavior":"deny","message": motivo}`.
- `server.py` (sem bpy):

```python
class McpServer:
    def __init__(self, handler: McpHandler, token: str, port: int = 0): ...
    def start(self) -> int     # ThreadingHTTPServer em thread daemon; devolve a porta
    def stop(self) -> None
    @property
    def url(self) -> str       # f"http://127.0.0.1:{port}/mcp"
```

  - `POST /mcp`:
    - sem `Authorization: Bearer <token>` correto → 401;
    - `Host` diferente de `127.0.0.1:<porta>` ou `localhost:<porta>` → 403;
    - corpo JSON-RPC → resposta `application/json`;
    - notificação → 202 sem corpo.
  - `GET /mcp` → 405.
  - A chamada a `handler.handle` roda na thread HTTP. Quem monta o handler (Task 13) passa um `call` que usa `run_on_main`.

- [ ] **Step 1: Testes.**
  - Protocolo puro:
    - `initialize` devolve `PROTOCOL_VERSION`;
    - `tools/list` sem `approve` não traz `approve_action`, e com `approve` traz;
    - `tools/call` com imagem gera um bloco `image` em base64;
    - método desconhecido → `-32601`;
    - `approve_action` negado → `behavior == "deny"`.
  - HTTP real com `urllib`:
    - sem token → 401 (Review Focus 4);
    - token errado → 401;
    - `Host: evil.com` → 403 (Review Focus 4);
    - `tools/list` válido → 200;
    - notificação → 202;
    - GET → 405.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: servidor MCP local com token`.

---

### Task 11: Ponte com o Claude Code (processo e parser)

**Files:** Create `claude/__init__.py`, `claude/stream.py`, `claude/process.py`, `scripts/record_fixture.py`, `tests/fake_claude.py`, `tests/fixtures/text_turn.jsonl`, `tests/fixtures/tool_turn.jsonl`; Test `tests/test_claude_bridge.py`

**Interfaces:**
- `stream.py` (sem bpy):

```python
@dataclass class SessionStarted: session_id: str
@dataclass class TextDelta: text: str
@dataclass class ToolStarted: tool_use_id: str; name: str; input: dict   # name sem o prefixo "mcp__blendercraft__"
@dataclass class ToolFinished: tool_use_id: str; output: str; is_error: bool
@dataclass class TurnFinished: is_error: bool; result: str
Event = SessionStarted | TextDelta | ToolStarted | ToolFinished | TurnFinished
def parse_line(line: str) -> list[Event]   # linha inválida ou desconhecida → []
```

  Mapeamento:
  - `system`/`init` → `SessionStarted`;
  - `stream_event` com `content_block_delta` `text_delta` → `TextDelta`. **O texto vem só dos deltas**; as mensagens `assistant` completas servem apenas para os blocos `tool_use`;
  - `assistant` → `ToolStarted` por bloco `tool_use`;
  - `user` → `ToolFinished` por bloco `tool_result`;
  - `result` → `TurnFinished`.
- `process.py`:

```python
def find_claude(pref_path: str = "") -> str | None
    # ordem: pref_path, shutil.which("claude"), ~/.local/bin/claude, /opt/homebrew/bin/claude, /usr/local/bin/claude
def build_args(claude: str, *, mcp_url: str, token: str, mode: str, model: str,
               system_prompt: str, plugin_dir: str, read_tools: list[str],
               write_tools: list[str], resume: str | None) -> list[str]
class ClaudeProcess:
    def __init__(self, args: list[str], cwd: str, on_line: Callable[[str], None], on_exit: Callable[[int], None]): ...
    def start(self) -> None            # Popen, stdin/stdout em pipe, thread leitora chama on_line
    def send_user(self, content: list[dict]) -> None
        # escreve {"type":"user","message":{"role":"user","content":content}} + "\n"
    def stop(self) -> None             # terminate; espera 3 s; kill
    @property
    def alive(self) -> bool
```

  - Base de `build_args`:

    ```
    [claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
     "--include-partial-messages", "--verbose", "--mcp-config", <json>, "--strict-mcp-config",
     "--tools", "", "--append-system-prompt", system_prompt, "--plugin-dir", plugin_dir]
    ```

    O `<json>` é `{"mcpServers":{"blendercraft":{"type":"http","url":mcp_url,"headers":{"Authorization":"Bearer "+token}}}}`.
  - Por modo:
    - `AUTO`: `--allowedTools mcp__blendercraft`;
    - `ASK`: `--allowedTools <read_tools com prefixo, separadas por vírgula>`, `--permission-prompts host` e `--permission-prompt-tool mcp__blendercraft__approve_action` (se o CLI rejeitar a combinação, gravar uma fixture do modo ASK no Step 1 e seguir o que funcionar);
    - `READONLY`: `--allowedTools <read_tools>`, `--disallowedTools <write_tools>` e `--permission-prompts none`.
  - Sufixos opcionais: `--model model` se `model` não for vazio; `--resume resume` se `resume` existir.

- [ ] **Step 1: Gravar as fixtures reais.**
  - `scripts/record_fixture.py` roda com o `python3` do sistema:
    - sobe `McpServer` com uma ferramenta falsa `echo` (read);
    - roda `claude` com `build_args(..., model="haiku")`;
    - envia a mensagem;
    - grava o stdout em `tests/fixtures/<nome>.jsonl`.
  - Gravar `text_turn` ("Responda apenas: oi") e `tool_turn` ("Chame a ferramenta echo com text='oi' e depois responda ok").
  - Conferir à mão que os formatos batem com o mapeamento acima. Se algum nome de campo for diferente, **o parser segue a fixture** e este plano é corrigido.
- [ ] **Step 2: Testes.**
  - `test_text_turn`: concatenar os `TextDelta` não dá vazio; aparecem exatamente um `SessionStarted` e um `TurnFinished(is_error=False)`; nenhum texto duplicado vindo da mensagem `assistant`.
  - `test_tool_turn`: `ToolStarted(name="echo")` seguido de `ToolFinished` com o mesmo `tool_use_id`.
  - `test_invalid_line`: `parse_line("lixo") == []`.
  - `test_build_args_modes`: confere as flags de cada modo; `--tools` seguido de `""`; JSON do MCP com o header.
  - `test_find_claude_without_path` (Review Focus 1): com `PATH` vazio e `HOME` temporário contendo `.local/bin/claude` executável, encontra esse caminho; sem nenhum, devolve `None`.
  - `test_process_with_fake`:
    - `tests/fake_claude.py` reproduz uma fixture a cada linha `user` recebida;
    - `ClaudeProcess` recebe todas as linhas;
    - `stop()` encerra e `alive` vira falso.
- [ ] **Step 3:** Rodar → falha.
- [ ] **Step 4:** Implementar.
- [ ] **Step 5:** Rodar → OK.
- [ ] **Step 6:** Commit `feat: ponte com o Claude Code em stream-json`.

---

### Task 12: Sessão do agente e conversas salvas

**Files:** Create `conversations.py`, `claude/session.py`; Test `tests/test_session.py`, `tests/test_conversations.py`

**Interfaces:**
- `conversations.py` (sem bpy):

```python
@dataclass class ToolCall: id: str; name: str; input: dict; output: str = ""; status: str = "running"  # running|ok|error|interrupted
@dataclass class Message: role: str; text: str = ""; tools: list[ToolCall] = field(default_factory=list)
@dataclass class Conversation: id: str; title: str; created: float; updated: float; blend_path: str; model: str; claude_session_id: str | None; messages: list[Message]
DEFAULT_ROOT = Path.home() / "Library/Application Support/BlenderCraft/conversations"
class ConversationStore:
    def __init__(self, root: Path = DEFAULT_ROOT): ...
    def new(self, blend_path: str, model: str) -> Conversation
    def save(self, conv: Conversation) -> None      # grava <id>.json de forma atômica (tmp + os.replace)
    def load(self, conv_id: str) -> Conversation
    def list(self, current_blend: str = "") -> list[Conversation]   # primeiro as do arquivo atual, depois por updated desc
    def delete(self, conv_id: str) -> None
```

  O título é a primeira mensagem do usuário, cortada em 60 caracteres. JSON corrompido é ignorado em `list` (o erro vai para o log).
- `session.py` (sem bpy):

```python
class AgentSession:
    def __init__(self, conversation: Conversation, store: ConversationStore,
                 spawn: Callable[[str | None], "ProcessLike"],   # recebe resume_id
                 tool_kinds: Callable[[str], str],               # nome → "read"|"scene"|"file"
                 max_tool_calls: int = 40): ...
    status: str          # "IDLE" | "WORKING" | "ERROR"
    queue: list[tuple[str, list[dict]]]
    changes: int         # ToolFinished ok de kind "scene" nesta conversa
    error: str
    def send(self, text: str, attachments: list[dict] = ()) -> None
    def send_now(self, text: str, attachments: list[dict] = ()) -> None   # stop() + send()
    def stop(self) -> None
    def handle_line(self, line: str) -> None     # chamado no thread principal
    def handle_exit(self, code: int) -> None
    def close(self) -> None                      # stop() + save; usado em Nova conversa e ao sair
```

  - `send`:
    - se `WORKING`, só enfileira;
    - se não, (re)cria o processo quando preciso com `spawn(conversation.claude_session_id)`;
    - acrescenta uma `Message("user")` e uma `Message("assistant")` vazia;
    - chama `send_user` com `[{"type":"text","text":text}, *attachments]` e muda `status` para `WORKING`.
  - Eventos:
    - `SessionStarted` → grava `claude_session_id`;
    - `TextDelta` → acrescenta ao texto do assistente;
    - `ToolStarted` / `ToolFinished` → atualizam `ToolCall`;
    - `TurnFinished` → `IDLE`, salva e envia o primeiro item da fila, se houver.
  - Mais de `max_tool_calls` `ToolStarted` num turno → `stop()` e o assistente recebe a nota `t("step_limit")`. O texto vem por um parâmetro `notes: dict[str, str]` do construtor, para manter o módulo sem bpy.
  - `stop()` → `process.stop()`, ferramentas `running` viram `interrupted`, `status` vira `IDLE` e a conversa é salva. O próximo `send` recria o processo com `--resume`.
  - Retomada falha (Review Focus 3): `TurnFinished(is_error=True)` ou `handle_exit(≠0)` antes de qualquer `SessionStarted` num processo criado com `resume`:
    - zera `claude_session_id`;
    - recria o processo sem `resume`;
    - reenvia a última mensagem do usuário precedida de `"Resumo da conversa anterior:\n"` + as últimas 10 mensagens em texto.
  - Saída inesperada no meio de um turno → `status = "ERROR"` e `error` recebe o código de saída.

- [ ] **Step 1: Testes.**
  - Conversas:
    - `test_roundtrip`;
    - `test_list_current_first`;
    - `test_corrupt_file_skipped`;
    - `test_delete`.
  - Sessão, com processo falso que grava `send_user` e permite injetar linhas das fixtures:
    - `test_text_turn_flow`: `IDLE` → `WORKING` → `IDLE`; o texto do assistente fica preenchido; a conversa é salva com `claude_session_id`;
    - `test_queue_while_working`: segundo `send` durante `WORKING` não chama `send_user`; depois do `TurnFinished`, chama;
    - `test_changes_count`: com `tool_kinds` devolvendo `"scene"`, um `ToolFinished` ok faz `changes == 1`;
    - `test_stop_interrupts` (Review Focus 5): com uma ferramenta `running`, `stop()` → o processo falso é parado, a ferramenta fica `interrupted`, o status vira `IDLE` e a conversa é salva;
    - `test_close_stops_process` (Review Focus 5);
    - `test_step_limit`: o 41º `ToolStarted` causa `stop()` e a nota aparece;
    - `test_resume_failure_fallback` (Review Focus 3): o primeiro spawn recebe `resume="abc"` e sai com código 1 → o segundo spawn recebe `None`, e o texto enviado começa com `"Resumo da conversa anterior:"`.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5:** Commit `feat: sessão do agente com fila, parada e conversas salvas`.

---

### Task 13: Painel, operadores, preferências e runtime

**Files:** Create `i18n.py`, `runtime.py`, `ui/__init__.py`, `ui/text_wrap.py`, `ui/props.py`, `ui/prefs.py`, `ui/operators.py`, `ui/panel.py`; Modify `__init__.py`; Test `tests/test_ui_logic.py`, `tests/test_register.py`

**Interfaces:**
- `i18n.py`:
  - `STRINGS: dict[str, dict[str, str]]` com as chaves `"pt"` e `"en"`;
  - `lang() -> str`: `"pt"` se `bpy.app.translations.locale` começar com `pt`, senão `"en"`;
  - `t(key: str, **fmt) -> str`.
  - Chaves mínimas: `assistant, idle, working, error, new_chat, conversations, stop, send, send_now, queued, attach_view, attach_selection, mode_auto, mode_ask, mode_readonly, changes, undo_ai, undo_ai_blocked, details, show_older, approve, deny, approval_request, claude_missing, claude_login, step_limit, unit, joint, joint_reset, copy_mcp`.
- `ui/text_wrap.py`:
  - `wrap(text: str, width_px: int, ui_scale: float) -> list[str]`: caracteres por linha = `max(10, int(width_px / (7 * ui_scale)))`, preservando quebras de parágrafo;
  - `strip_markdown(text: str) -> str`: remove `**`, `__`, crases, `#` no início de linha e links `[x](y)` → `x`.
- `runtime.py`: singleton com `server: McpServer`, `token`, `store`, `session: AgentSession | None`, `tracker: ChangeTracker`, `pending_approval: tuple[str, dict, threading.Event, list] | None`.
  - `start()`: cria o token, instala o timer, liga o handler `depsgraph_update_post` do tracker e sobe o servidor. A porta vem de `prefs.external_port` (0 = aleatória). O `call` do handler é `lambda n, a: run_on_main(lambda: call_tool(n, a, tracker))`. O `approve` grava `pending_approval`, chama `tag_redraw` e espera o `Event` por até 600 s (esgotado = negar).
  - `stop()`: fecha a sessão, para o servidor, desinstala o timer e remove os handlers.
  - `ensure_session()`: cria a `AgentSession`. O `spawn` usa `find_claude(prefs.claude_path)` e `build_args` com:
    - `mode` e `model` das props;
    - o `plugin_dir` empacotado;
    - `build_system_prompt(...)` (Task 14; até lá, uma string fixa);
    - `cwd = ~/Library/Application Support/BlenderCraft/workspace` (criado se não existir).
    As linhas do processo voltam por `post(lambda: session.handle_line(l))`, seguidas de redraw.
  - Trocar modo ou modelo → `session.stop()`; o próximo envio recria o processo com `--resume`.
- `ui/props.py`: `WindowManager.bc` (`PointerProperty`, não é salvo no `.blend`) com:
  - `input: StringProperty`, cujo `update` envia ao apertar Enter se o texto não estiver vazio;
  - `mode: EnumProperty(AUTO, ASK, READONLY)`;
  - `model: EnumProperty("", opus, sonnet, haiku, fable)`;
  - `attach_view`, `attach_selection: BoolProperty`;
  - `show_older: IntProperty`;
  - `expanded_tool: StringProperty`;
  - `joint_pivot: StringProperty`, `joint_angle: FloatProperty` (o `update` chama `set_angle`).
- `ui/prefs.py`: `AddonPreferences` com `claude_path: StringProperty(subtype="FILE_PATH")` e `external_port: IntProperty(default=0)`.
- `ui/operators.py` (prefixo `bc.`):
  - `send`, `send_now`, `stop`, `new_chat`, `open_conversation(conv_id)`, `delete_conversation(conv_id)`, `toggle_details(tool_id)`;
  - `undo_ai_changes`: se `can_undo_last_turn()` for falso, mostra `t("undo_ai_blocked")` e abre `ed.undo_history`;
  - `approve(allow: bool)`;
  - `set_unit(unit)`;
  - `joint_create`: usa a peça ativa, o cursor 3D como ponto e o eixo Z por padrão;
  - `joint_reset`;
  - `copy_mcp_command`: copia `claude mcp add --transport http blendercraft <url> --header "Authorization: Bearer <token>"`.
  - Os anexos (vista e seleção) são montados em `send`:
    - vista: `screenshot("viewport")` → bloco `{"type":"image","source":{"type":"base64","media_type":"image/png","data":…}}`;
    - seleção: bloco de texto com o resultado de `inspect_object` de cada selecionado.
- `ui/panel.py`: `VIEW3D_PT_bc_assistant` (`bl_space_type="VIEW_3D"`, `bl_region_type="UI"`, `bl_category = t("assistant")` na hora do registro). Contém, nesta ordem, os elementos da tabela "O painel do assistente" da spec, mais:
  - um bloco de aprovação no topo quando `pending_approval` existir;
  - um aviso com `t("claude_missing")` ou `t("claude_login")` quando `find_claude` falhar ou o processo sair por falta de login;
  - um subpainel "Articulação" (`joint_create`, slider de ângulo, `joint_reset`, botão que roda `joint_test` e mostra o resultado);
  - um seletor de unidade.
- `__init__.py`: `register()` registra classes e props e chama `runtime.start()` num timer de primeiro tick; `unregister()` faz o inverso. As ferramentas são registradas ao importar `tools`.

- [ ] **Step 1: Testes.**
  - `test_strings_complete`: toda chave tem `pt` e `en` não vazios.
  - `test_wrap_width`: com `width_px=280` e `ui_scale=1`, nenhuma linha passa de 40 caracteres.
  - `test_wrap_keeps_paragraphs`.
  - `test_strip_markdown`.
  - `test_register_unregister`: registrar a extensão em `-b`, conferir que `bpy.types.VIEW3D_PT_bc_assistant` existe e que o servidor responde a `tools/list` com token; depois `unregister()`, e o servidor para.
  - `test_tools_list_contains_all`: a lista expõe `list_operators, describe_operator, run_python, inspect_scene, inspect_object, measure, mesh_topology, print_check, screenshot, joint_create, joint_set_angle, joint_test, undo, redo, import_file, export_file, save`.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5: Checagem manual na interface.**
  - Ligar a extensão por symlink num Blender comum.
  - Abrir a aba Assistente e mandar "crie um cubo de 20 mm".
  - O texto chega aos poucos e a chamada `run_python` aparece com Detalhes.
  - Ctrl+Z desfaz o cubo.
  - Enfileirar uma mensagem durante o trabalho.
  - Parar no meio.
  - No modo Perguntar, Aprovar e Recusar funcionam.
- [ ] **Step 6:** Commit `feat: painel do assistente integrado ao Claude Code`.

---

### Task 14: Prompt de sistema e plugin de designer

**Files:** Create `claude/system_prompt.py`, `plugin/.claude-plugin/plugin.json`, `plugin/skills/blender-map/SKILL.md`, `plugin/skills/design/SKILL.md`, `plugin/skills/printing/SKILL.md`, `plugin/skills/joints/SKILL.md`; Modify `runtime.py` (usar `build_system_prompt`); Test `tests/test_prompt_plugin.py`

**Interfaces:**
- `build_system_prompt(blender_version: str, display_unit: str, language: str) -> str` (`language` é `"pt"` ou `"en"`). O texto informa versão, unidade e idioma de resposta, e traz as regras de trabalho da spec:
  - seguir instruções à risca e perguntar quando algo for ambíguo;
  - inspecionar antes de alterar;
  - preferir operações não destrutivas;
  - um passo de ferramenta por ação lógica, com `description` curta;
  - depois de mudanças visíveis, `screenshot` de pelo menos dois ângulos e autoavaliação antes de concluir;
  - dizer o que fez e o que não conseguiu;
  - consultar as skills de design, impressão e articulação quando forem relevantes.
- `plugin.json`: `{"name":"blendercraft","version":"0.1.0","description":"Conhecimento de modelagem, design e impressão 3D para o BlenderCraft"}`.
- Skills (frontmatter `name` e `description`; conteúdo prático, curto e acionável):
  - `blender-map`: o que fazem os modificadores principais (Bevel, Boolean, Solidify, Mirror, Array, Remesh, Decimate, Weld), as ferramentas de malha, Geometry Nodes, materiais e unidades, e qual caminho é mais robusto para cada tipo de pedido;
  - `design`: proporção, hierarquia de formas, simetria, raios de arredondamento e chanfro consistentes, silhueta, e sinais de algo "amador";
  - `printing`: parede ≥ 0.8 mm (bico de 0.4 mm), balanço de 45°, orientação, suportes, folgas (encaixe 0.2–0.3 mm, articulação print-in-place 0.3–0.5 mm) e detalhe mínimo;
  - `joints`: receita de pino e encaixe com folga entre peças (pino, furo com folga, trava), seguida da validação com `joint_test` e `mesh_topology`.

- [ ] **Step 1: Testes.**
  - `test_prompt_contains_context`: a unidade `"in"` e a regra de screenshot aparecem; para `language="en"`, o texto pede resposta em inglês.
  - `test_plugin_valid`: `plugin.json` é JSON válido com `name == "blendercraft"`; cada `SKILL.md` tem frontmatter com `name` igual ao nome da pasta e `description` não vazia.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Escrever prompt e skills.
- [ ] **Step 4:** Rodar → OK.
- [ ] **Step 5: Checagem manual.**
  - Pedir no painel: "crie um suporte de celular bonito e imprimível".
  - O agente consulta as skills, tira prints de dois ângulos, comenta a própria estética e roda `print_check`.
- [ ] **Step 6:** Commit `feat: prompt de sistema e skills de designer`.

---

### Task 15: Template de aplicação, instalador e teste de ponta a ponta

**Files:** Create `app_template/blendercraft/__init__.py`, `scripts/install.sh`, `scripts/BlenderCraft.command`, `README.md` (instalar, abrir, requisitos: Claude Code instalado e logado); Test `tests/test_template.py`

**Interfaces:**
- Template `register()`:
  - habilita `bl_ext.user_default.blendercraft_assistant` (`addon_utils.enable(..., default_set=True, persistent=True)`);
  - `preferences.edit.undo_steps = 256`;
  - adiciona o handler persistente `load_post` → `setup_new_file()`;
  - agenda um timer de uma vez que, com janela disponível, fecha a área TIMELINE do Layout (`screen.area_close` com `temp_override`) e liga `show_region_ui` nas 3D Views.
- `setup_new_file() -> None`: só age quando `bpy.data.filepath == ""`. Nesse caso:
  - `apply_internal_mm(scene)`;
  - o cubo padrão passa a ter 20 mm;
  - remove a workspace `Animation`.
  Arquivos com nome não são tocados.
- `unregister()` remove o handler.
- `install.sh [--dev]`:
  - `CONFIG="$HOME/Library/Application Support/Blender/5.2"`;
  - copia (ou, com `--dev`, cria symlink de) `app_template/blendercraft` → `$CONFIG/scripts/startup/bl_app_templates_user/blendercraft` e `blendercraft_assistant` → `$CONFIG/extensions/user_default/blendercraft_assistant`;
  - falha com mensagem clara se `/Applications/Blender.app` não existir;
  - avisa, sem falhar, se `claude` não for encontrado nos caminhos da Task 11.
- `BlenderCraft.command`: `exec /Applications/Blender.app/Contents/MacOS/Blender --app-template blendercraft "$@"`.

- [ ] **Step 1: Testes.**
  - `test_setup_new_file`: em `-b`, depois de `setup_new_file()` → `scale_length == 0.001`, `"Animation" not in bpy.data.workspaces` e o cubo com `dimensions` ≈ (20, 20, 20).
  - `test_named_file_untouched`: salvar num arquivo temporário com `scale_length = 1`, reabrir e rodar `setup_new_file()` → nada muda.
- [ ] **Step 2:** Rodar → falha.
- [ ] **Step 3:** Implementar o template e os scripts.
- [ ] **Step 4:** Rodar → OK, e a suíte inteira (`RUN_TESTS`) → OK.
- [ ] **Step 5: Checagem manual de ponta a ponta** (lista da spec).
  - `scripts/install.sh` e abrir `BlenderCraft.command`.
  - Não há aba Animation nem timeline, e o painel Assistente está aberto.
  - Pedir "crie um cubo de 20 mm com furo de 5 mm e exporte 3MF em ~/Desktop/teste.3mf".
  - Ver o streaming; parar no meio; enfileirar.
  - Trocar a unidade para polegadas: as medidas exibidas mudam e o STL/3MF continua em 20 mm.
  - Testar uma articulação pelo subpainel.
  - Desfazer mudanças da IA.
  - Fechar o Blender, reabrir e continuar a conversa pela lista Conversas.
  - Conferir com `ps` que nenhum `claude` ficou órfão.
- [ ] **Step 6:** Commit `feat: template BlenderCraft, instalador e lançador`.
