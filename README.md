# BlenderCraft

BlenderCraft é o Blender 5.2 com um painel de assistente na lateral da viewport. O assistente é o seu próprio Claude Code, rodando dentro do Blender: você descreve o que quer ("crie um cubo de 20 mm com um furo de 5 mm e exporte em 3MF") e ele modela, mede, confere a malha e exporta.

O projeto tem duas partes:

- **Template de aplicação `blendercraft`** (`app_template/`): abre o Blender sem a tela de splash, sem a workspace Animation e sem a timeline, num layout parecido com o do SolidCraft (veja [Layout](#layout)), com o painel Assistente aberto, unidades em milímetros (1 unidade do Blender = 1 mm) e 256 passos de desfazer. O resto do Blender fica como sempre foi. Arquivos `.blend` que já têm nome abrem com as workspaces, o layout e as unidades deles.
- **Extensão `blendercraft_assistant`** (`blendercraft_assistant/`): o painel, o servidor MCP local e a ponte com o Claude Code.

## Requisitos

- macOS.
- Blender 5.2 instalado em `/Applications/Blender.app`.
- Claude Code instalado e com login feito. Se ainda não fez, rode `claude` uma vez no terminal e entre na sua conta. O painel avisa se o `claude` não for encontrado ou se o login estiver faltando.
- Não precisa de mais nada: a extensão usa só a biblioteca padrão do Python.

## Instalar

No terminal, na pasta do projeto:

```sh
scripts/install.sh
```

O script copia o template para a pasta de configuração do Blender (`~/Library/Application Support/Blender/5.2`) e instala a extensão no repositório `user_default`. Ele falha com uma mensagem clara se o Blender não estiver em `/Applications`, e só avisa (sem falhar) se não achar o `claude`.

Rode de novo depois de atualizar o projeto, para copiar a versão nova.

## Instalar o app BlenderCraft

```sh
scripts/install.sh --app
```

Além do template e da extensão, cria o **BlenderCraft.app** em `~/Applications` (a pasta é criada se não existir) e o registra no Finder. Depois disso o BlenderCraft aparece no Launchpad e no Spotlight, com o ícone próprio. Dá para combinar com `--dev` (`scripts/install.sh --dev --app`).

Para só gerar o app, sem instalar: `scripts/build_app.sh` cria `dist/BlenderCraft.app`. O ícone vem de `scripts/assets/BlenderCraft.png`, que é desenhado por `scripts/make_icon.py` (rode de novo com o Blender para regenerar, o comando está no topo do arquivo).

O app só abre o Blender que está em `/Applications/Blender.app` com o template `blendercraft`; ele não traz uma cópia do Blender.

## Abrir

Pelo app: dois cliques no **BlenderCraft** (Launchpad, Spotlight ou `~/Applications`).

Pelo terminal:

```sh
scripts/BlenderCraft.command
```

A extensão é habilitada pelo próprio template, e a aba **Assistente** já abre na barra lateral (tecla N) da 3D View.

### Abrir arquivos STL e 3MF

- **No Finder**: clique com o botão direito no arquivo `.stl` ou `.3mf`, **Abrir com › BlenderCraft**. O BlenderCraft não vira o app padrão desses arquivos (nem dos `.blend`); ele só aparece na lista do **Abrir com**. Para torná-lo padrão, use **Obter Informações › Abrir com › Alterar Tudo** no Finder.
- **Arrastando** arquivos para o ícone do BlenderCraft no Dock ou no Finder.
- **Pelo terminal**: `scripts/BlenderCraft.command peca.stl --open suporte.3mf` (o `--open` é opcional).

O BlenderCraft abre um arquivo novo, importa cada arquivo em milímetros (o 3MF usa a unidade declarada no arquivo), tira o cubo inicial se ele não foi mexido e enquadra os objetos importados na 3D View. Se algum arquivo não abrir, aparece um aviso com o motivo. Um `.blend` aberto do mesmo jeito abre como arquivo do Blender, com o template ativo.

Com o BlenderCraft já aberto, dá para arrastar arquivos `.3mf` para dentro da 3D View, e o menu **File** tem importar e exportar 3MF.

### Layout

Arquivos novos abrem no layout **SolidCraft**: à esquerda, o **Browser** (o Outliner, com os olhos para mostrar e esconder objetos) em cima e as Propriedades embaixo; no meio, a 3D View indo até a borda direita da janela; e o chat do Assistente na barra lateral, flutuando sobre a viewport. Não há timeline.

Quem prefere o Blender de sempre escolhe em **Window › Layout do BlenderCraft › Blender clássico**: Outliner e Propriedades na coluna da direita, nos tamanhos padrão do Blender, com o Assistente na barra lateral. A escolha vale para os próximos arquivos novos (**File › New**) e fica salva em `~/Library/Application Support/BlenderCraft/settings.json`. Arquivos `.blend` com nome sempre abrem com o layout que foi salvo neles.

A largura da barra lateral não pode ser definida por script no Blender 5.2; ela abre na largura padrão e dá para arrastar a borda dela para alargar o chat.

O BlenderCraft não mostra a tela de splash ao abrir. Isso não muda a preferência do Blender comum, que continua mostrando a splash.

## Modo de desenvolvimento

```sh
scripts/install.sh --dev
```

Em vez de copiar, cria links simbólicos para as pastas do projeto. As edições no código valem na próxima vez que o Blender abrir, sem reinstalar. Para voltar à instalação por cópia, rode `scripts/install.sh` sem opções.

## Rodar os testes

Os testes rodam dentro do Blender em modo de fundo, a partir da pasta do projeto:

```sh
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python-exit-code 1 --python tests/run.py
```

Para rodar só alguns testes, acrescente `-- -k PADRÃO`, por exemplo `-- -k template`. O comando termina com status diferente de zero se algum teste falhar.

## Modos de permissão

Ficam no painel:

- **Automático** (padrão): o assistente executa tudo sozinho. Você pode desfazer o que ele fez com **Desfazer mudanças da IA**.
- **Perguntar antes de alterar**: cada ferramenta que modifica a cena mostra **Aprovar** e **Recusar** no painel. As ferramentas só de leitura rodam direto.
- **Só leitura**: só as ferramentas de inspeção estão disponíveis. O `run_python` não é oferecido.

## Unidades

Por dentro, 1 unidade do Blender é sempre 1 mm. Você escolhe a unidade exibida no painel (mm, cm, m, polegadas ou pés), e isso muda só o que aparece na tela. Os arquivos STL e 3MF saem sempre em milímetros, que é o que os fatiadores esperam.

## Onde ficam as conversas

As conversas são salvas em `~/Library/Application Support/BlenderCraft/conversations/`. Pela lista **Conversas** do painel você reabre uma conversa antiga e continua de onde parou, ou apaga (com confirmação). O Claude Code roda na pasta `~/Library/Application Support/BlenderCraft/workspace`.

Chaves e tokens nunca são gravados no `.blend`.

## O que o Claude embutido pode fazer

O Claude Code do painel roda com as ferramentas embutidas desligadas, **exceto a ferramenta `Skill`**, que ele precisa para usar as skills do projeto (design, impressão 3D, articulações e o mapa do Blender). Ele não tem Bash, Edit, Write nem WebFetch: trabalha na cena pelas ferramentas do BlenderCraft (servidor MCP local, em `127.0.0.1`, protegido por um token aleatório a cada sessão), e o `run_python` cobre o que mais for preciso.

O processo do `claude` é encerrado ao fechar o Blender ou ao começar uma nova conversa.

## Preferências da extensão

Em Preferências, Extensões, BlenderCraft Assistant, dá para indicar o caminho do `claude` (quando ele não está no PATH nem nos lugares usuais), fixar a porta do servidor MCP e copiar o comando `claude mcp add`, que liga um Claude Code do terminal ao mesmo Blender (o botão **Copiar comando MCP** também fica no painel).

O token do servidor MCP é novo a cada vez que o Blender abre (ou a extensão é religada) e não é gravado em lugar nenhum. Por isso, depois de reiniciar o Blender, copie o comando de novo e rode-o outra vez no terminal; como o nome `blendercraft` já estará registrado, remova o antigo antes com `claude mcp remove blendercraft`.

## Licença

GPL-3.0-or-later.
