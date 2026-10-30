# BlenderCraft — Design

Data: 2026-10-30 · Status: revisado, aguardando aprovação

## Intenção

Um app para **criar e editar objetos 3D**, muitas vezes peças para impressão 3D como STL e 3MF. Ele tem tudo o que o Blender tem, **menos a parte de animação à vista**. E traz o **Claude embutido na lateral**, trabalhando dentro do próprio programa: cria, modifica, mede, separa peças, testa articulações e exporta, sem precisar de um chat externo. A referência de experiência é o painel "Assistant" do SolidCraft (solidcraft.io).

O assistente deve entender o que cada função do Blender faz, ter **olhar de designer** (saber o que fica bonito ou não) e **seguir instruções à risca**.

## Decisões do usuário

- **A base é o próprio Blender, não um app novo.** O Blender é open source (GPL), então usamos a API Python dele, sem engenharia reversa. Do SolidCraft tiramos só inspiração de experiência de uso, sem copiar código nem marca.
- **A animação é escondida, não removida do código-fonte.** Não vamos recompilar o Blender. Se esconder algo quebrar outra coisa, fica igual ao Blender.
- **O chat usa o visual nativo do Blender**, num painel na barra lateral.
- **O Claude roda pelo login do Claude Code.** O painel usa o Claude Code CLI já instalado no Mac (testado com 2.1.310), com a conta do usuário e sem chave de API. O Blender roda **dentro dele** um servidor MCP com as ferramentas, e o Claude Code se conecta a esse servidor. Outras IAs (Grok, GPT, modelos locais) ficam para depois.
- **`run_python` roda sem restrição**, como o console de scripts do Blender. A proteção vem dos modos de permissão (ver abaixo).
- **Unidades:**
  - o padrão é milímetros, mas o usuário pode trocar para cm, m, polegadas ou pés;
  - STL e 3MF precisam sair no tamanho certo para o fatiador.
- **Idiomas:** português e inglês.
- **As respostas aparecem em streaming.**
- **As conversas ficam salvas dentro do app.**
- **Exportação 3MF é obrigatória**, para imprimir.
- **Articulação entra**, sem usar o sistema de animação (proposta aceita como recomendação; detalhes abaixo).
- **Só macOS por enquanto.**
- **Blender alvo: 5.2.2 LTS** (em `/Applications/Blender.app`, com Python 3.13 embutido).

## Arquitetura

```
┌──────────────── Blender 5.2 (template BlenderCraft) ────────────────┐
│                                                                     │
│  Painel "Assistente" (UI nativa)  ◄── eventos ──┐                   │
│        │ mensagens do usuário                   │                   │
│        ▼                                        │                   │
│  Ponte Claude Code ── subprocesso ──► claude -p (stream-json)        │
│                                        │  ▲                         │
│                                        │  │ chamadas de ferramenta  │
│                                        ▼  │                         │
│  Servidor MCP (HTTP em 127.0.0.1, thread de fundo)                  │
│        │ fila para o thread principal (bpy.app.timers)              │
│        ▼                                                            │
│  Ferramentas (bpy) ── passos de desfazer nomeados                   │
└─────────────────────────────────────────────────────────────────────┘
```

**Ponte Claude Code.** A cada conversa o painel inicia um processo `claude` em modo headless:
- `--print --input-format stream-json --output-format stream-json --include-partial-messages --verbose` mantém o processo vivo entre mensagens, com texto chegando em streaming;
- `--mcp-config` aponta para o servidor HTTP local, e `--strict-mcp-config` impede que outros servidores MCP do usuário entrem nessa sessão;
- `--tools` / `--allowedTools` liberam só as ferramentas do BlenderCraft. As ferramentas embutidas do Claude Code (Bash, Edit, Write, WebFetch etc.) ficam desligadas por padrão *(decisão minha: o agente trabalha na cena, não no disco do usuário; o `run_python` já cobre o que for preciso)*;
- `--plugin-dir` carrega um plugin do BlenderCraft com o conhecimento de designer (ver "Inteligência");
- `--append-system-prompt` adiciona o contexto da sessão: versão do Blender, unidade atual, idioma e regras de trabalho;
- `--model` vem do seletor de modelo do painel;
- `--session-id` / `--resume` permitem retomar conversas salvas.

O processo é encerrado ao fechar o Blender ou ao começar uma nova conversa. Se o `claude` não estiver instalado ou logado, o painel mostra o que fazer (instalar ou rodar `claude` uma vez para fazer login), em vez de falhar em silêncio.

**Servidor MCP interno.** Usa MCP sobre HTTP (streamable HTTP, JSON-RPC) com a biblioteca padrão do Python, ouvindo só em `127.0.0.1`, numa porta livre escolhida ao iniciar. Exige um token aleatório por sessão no cabeçalho, para outros programas da máquina não usarem o servidor sem permissão *(decisão minha)*. Também pode ser ligado sob demanda para o Claude Code do terminal controlar o mesmo Blender: o painel mostra o comando `claude mcp add` pronto para copiar.

**Thread principal.** O HTTP roda em thread de fundo. Toda chamada que toca `bpy` entra numa fila executada no thread principal via `bpy.app.timers`, e a resposta volta para o servidor. Assim a interface nunca trava.

## Template de aplicação "BlenderCraft"

Pasta `bl_app_templates_user/blendercraft`. Ao abrir:
- remove a workspace *Animation* e a timeline da tela inicial e mantém todas as outras workspaces padrão. Armaduras e o Pose Mode (pelo menu de modo) continuam disponíveis;
- **só isso é escondido.** Menus de keyframe, tecla I, Dope Sheet e Graph Editor ficam como no Blender. Arquivos `.blend` de terceiros abrem com as workspaces deles;
- abre com o painel do assistente visível na lateral direita da viewport;
- configura unidades (ver abaixo) e sobe `undo_steps` para 256;
- habilita a extensão do assistente.

**Lançador:** `install.sh` instala o template e a extensão na pasta de configuração do Blender do usuário. `BlenderCraft.command` abre `Blender --app-template blendercraft`. *(Decisão minha: um `.app` empacotado fica para depois.)*

**Extensão "BlenderCraft Assistant":** formato de extensão do Blender (`blender_manifest.toml`), licença GPL-3.0-or-later.

## Unidades

- Por dentro, **1 unidade do Blender = 1 mm, sempre** (`scale_length = 0.001`). O usuário só troca **a unidade exibida** (mm, cm, m, polegadas, pés) num seletor no painel e no cabeçalho da viewport; isso muda `length_unit` / `system` *(decisão minha)*.
- Assim STL (que não guarda unidade) e 3MF (`unit="millimeter"`) sempre saem em mm, que é o que os fatiadores esperam, qualquer que seja a unidade exibida. Ao importar STL, o padrão é assumir mm.
- O agente recebe a unidade exibida e responde nela ("20 mm", "0,79 in").

## O painel do assistente (adaptado do SolidCraft)

Aba "Assistente" na barra lateral (região N) da 3D View:

| Elemento | Comportamento |
|---|---|
| Cabeçalho | Título, selo de estado (*Ocioso* / *Trabalhando…* / *Erro*), **Nova conversa** e **Conversas** (lista das salvas). |
| Modelo | Dropdown com os modelos Claude disponíveis no Claude Code. |
| **Parar** | Visível enquanto o agente trabalha; interrompe a resposta atual. |
| Conversa | Mensagens com quebra de linha conforme a largura do painel. O texto chega em **streaming**. O Markdown é simplificado para texto puro. Mostra as últimas mensagens, com "mostrar anteriores". |
| Chamadas de ferramenta | Uma linha cada: ícone de estado (rodando/ok/erro), nome e resumo. **Detalhes** expande entrada e saída (truncadas). |
| Campo de mensagem | Uma linha; Enter envia. **Enquanto o agente trabalha, a mensagem vai para a fila.** **Enviar agora** interrompe e envia já. |
| Anexar vista | Checkbox: inclui um print da viewport na próxima mensagem. |
| Anexar seleção | Checkbox: inclui nome, tipo, dimensões e vértices/faces dos objetos selecionados. |
| Modo de permissão | **Automático** (padrão), **Perguntar antes de alterar** (cada ferramenta que modifica mostra Aprovar/Recusar no painel, via `--permission-prompt-tool` apontando para uma ferramenta do nosso MCP) e **Só leitura** (só ferramentas de inspeção são liberadas; `run_python` não é oferecido). |
| Contador de mudanças | "N mudanças" nesta conversa + **Desfazer mudanças da IA**, que desfaz o último pedido inteiro. Se o usuário editou à mão depois, o botão não age sozinho: avisa e abre *Edit › Undo History* *(decisão minha)*. |

**Idioma:** textos do painel em português e inglês, seguindo o idioma da interface do Blender (`bpy.app.translations`); português se o Blender estiver em pt_BR, inglês nos demais casos *(decisão minha)*.

Fora da v1: destacar o painel em janela própria, "Pick context" clicando em objetos, anexar arquivos e imagens de referência.

## Conversas salvas

- Cada conversa é salva como JSON em `~/Library/Application Support/BlenderCraft/conversations/`. O arquivo guarda as mensagens, as chamadas de ferramenta, o modelo, a data e o caminho do `.blend` em que ela aconteceu. Também guarda o `session_id` do Claude Code, para que **Continuar** retome a mesma sessão com memória completa *(decisão minha)*.
- A lista **Conversas** mostra primeiro as do arquivo aberto e depois as demais. Também dá para apagar uma conversa.
- Se o Claude Code não conseguir retomar a sessão (por exemplo, se foi apagada), a conversa abre só para leitura e uma nova começa com um resumo dela.

## Ferramentas do agente (servidor MCP)

Seguem a mesma divisão das ferramentas MCP do SolidCraft (descobrir / modelar / inspecionar / ver / continuar):

| Ferramenta | O que faz |
|---|---|
| `list_operators(query)` | Busca operadores `bpy.ops` por nome/descrição, por introspecção do próprio Blender. |
| `describe_operator(id)` | Parâmetros, tipos e valores padrão de um operador. |
| `run_python(code, description)` | Executa `bpy` no thread principal; devolve stdout, valor de retorno ou traceback. É a ferramenta principal de modelagem. |
| `inspect_scene()` | Objetos, hierarquia, transformações, materiais, modificadores, unidades e seleção. |
| `inspect_object(name)` | Dimensões, bounding box, vértices/faces e modificadores de um objeto. |
| `measure(...)` | Distâncias, volume, área e bounding box. |
| `mesh_topology(name)` | Peças soltas (componentes conectados), se é manifold, bordas abertas e normais invertidas. |
| `print_check(name)` | Checagem para impressão: espessura mínima de parede, balanços acima de um ângulo e peças fora da mesa *(decisão minha: o Blender 5.2 não traz o 3D-Print Toolbox embutido, então fazemos uma versão simples)*. |
| `screenshot(view, angle)` | Print da viewport, ou de um ângulo pedido (frente, lado, topo, isométrico), devolvido como imagem. |
| `joint_test(...)` | Ver "Articulação". |
| `undo()` / `redo()` | Desfaz/refaz passos. |
| `import_file(path)` / `export_file(objects, path, format)` | STL, OBJ, PLY, glTF (nativos) e **3MF** (nosso). |
| `save()` | Salva o `.blend`. |
| `approve_action(...)` | Interna: usada pelo modo *Perguntar antes de alterar*. |

Cada chamada que modifica a cena vira **um passo de desfazer nomeado** (ex.: "IA: separar pernas"), visível em *Edit › Undo History*. Esse histórico faz o papel da barra *History* do SolidCraft. O desfazer cobre os dados da cena; arquivos gravados em disco e preferências alteradas não voltam com Ctrl+Z.

## 3MF

O Blender 5.2 não lê nem grava 3MF, então implementamos isso em Python puro (3MF é um zip com XML):
- **Exportar (obrigatório):** um ou vários objetos, cada um como objeto separado no 3MF, com `unit="millimeter"`, transformações aplicadas e **cor por objeto** a partir do material (`basematerials`), para o fatiador já mostrar as peças coloridas.
- **Importar (desejável na v1):** malhas, nomes de objetos e cores básicas. Fica para depois se atrasar a exportação.

## Articulação (sem sistema de animação)

1. **Teste de articulação**
   - O usuário (ou a IA) escolhe uma peça móvel, um ponto de giro e um eixo. O app cria um *empty* como pivô e torna a peça filha dele.
   - Um controle deslizante no painel gira a peça; um botão **Voltar à posição** desfaz o giro.
   - A ferramenta `joint_test` varre o ângulo em passos, detecta colisão com as outras peças (BVH) e devolve a faixa livre, por exemplo: "gira de −40° a +65° antes de bater no tronco".
   - Nenhum keyframe é criado.
2. **Articulações para impressão (print-in-place)**
   - A IA sabe criar pinos e encaixes com folga configurável (padrão 0,3 mm) entre duas peças, como no cachorrinho articulado da referência.
   - Depois de criar, ela valida o resultado com `joint_test` e `mesh_topology`.

## Inteligência: olhar de designer

Entregue como um **plugin do Claude Code** que o BlenderCraft carrega (`--plugin-dir`), com skills em Markdown:
- **Mapa do Blender:** o que cada área faz (modificadores, ferramentas de malha, Geometry Nodes, materiais, unidades, exportação) e qual caminho é mais robusto para cada tipo de pedido, de preferência não destrutivo.
- **Design:** proporção, hierarquia de formas, simetria, consistência de raios de arredondamento e chanfro, silhueta, e quando algo parece "bonito" ou "amador".
- **Impressão 3D:** espessura de parede, balanços, suportes, orientação, folgas de encaixe e de articulação, e tamanho mínimo de detalhe.
- **Forma de trabalhar:**
  - seguir as instruções do usuário à risca e perguntar quando algo é ambíguo;
  - inspecionar antes de alterar;
  - depois de mudanças visíveis, **tirar prints de vários ângulos e se autoavaliar** (proporção, acabamento, problemas) antes de dizer que terminou;
  - dizer com clareza o que fez e o que não conseguiu.

## Restrições técnicas

- A interface nunca trava (ver "Thread principal").
- Limite de passos por pedido (ex.: 40 chamadas de ferramenta); ao atingir, o agente para e avisa.
- Saídas grandes de ferramentas são truncadas.
- O Blender vem com "Allow Online Access" desligado. A extensão não faz requisições de rede próprias: quem acessa a internet é o processo do Claude Code, e o servidor MCP é local. Por isso a extensão não precisa da permissão de rede do manifesto *(decisão minha; revisar se o Blender exigir)*.

## Fora do escopo (v1)

- Recompilar o Blender ou remover código de animação.
- Outras IAs além do Claude (o painel e o MCP devem permitir isso no futuro, mas a v1 só fala com o Claude Code).
- Windows e Linux.
- Modelagem paramétrica estilo CAD com histórico de features.
- Chat com visual web/HTML.

## O que faria isso ser um fracasso mesmo funcionando

- A IA alterar a cena de um jeito que não dá para desfazer com Ctrl+Z.
- O Blender congelar enquanto espera o Claude.
- Uma peça exportada chegar no fatiador no tamanho errado.
- Sumir alguma função de modelagem do Blender por causa do template.
- A IA declarar "pronto" sem ter conferido o resultado.

## Testes

- **Automatizados**, com o Blender em modo headless (`Blender -b --python tests/run.py`):
  - ferramentas contra cenas de teste (por exemplo, cubo + esfera soltos → `mesh_topology` acha 2 componentes);
  - exportação 3MF: zip válido, unidade em mm, cores e contagem de triângulos;
  - unidades: um cubo de 20 mm exportado em STL tem 20 de lado em qualquer unidade exibida;
  - `joint_test` com colisão conhecida;
  - servidor MCP respondendo a `initialize`, `tools/list` e `tools/call`;
  - passo de desfazer por chamada.
- **Ponte Claude Code** testada com um processo `claude` falso que emite stream-json gravado.
- **Checagem manual na interface:**
  - abrir pelo lançador;
  - pedir "crie um cubo de 20 mm com furo de 5 mm e exporte 3MF";
  - ver o texto chegando em streaming;
  - parar no meio e enfileirar uma mensagem;
  - trocar a unidade para polegadas;
  - testar uma articulação;
  - desfazer as mudanças da IA;
  - fechar e retomar a conversa.

## Deixado para quem implementar

Estrutura interna de módulos, esquemas exatos das ferramentas, layout fino do painel, texto exato das skills e do prompt, como truncar saídas, e a lista de modelos no seletor (lida do Claude Code quando possível).
