# BlenderCraft

BlenderCraft é o Blender 5.2 com um painel de assistente na lateral da viewport. O assistente é o seu próprio Claude Code, rodando dentro do Blender: você descreve o que quer ("crie um cubo de 20 mm com um furo de 5 mm e exporte em 3MF") e ele modela, mede, confere a malha e exporta.

O projeto tem duas partes:

- **Template de aplicação `blendercraft`** (`app_template/`): abre o Blender sem a workspace Animation e sem a timeline, com o painel Assistente visível, unidades em milímetros (1 unidade do Blender = 1 mm) e 256 passos de desfazer. O resto do Blender fica como sempre foi. Arquivos `.blend` que já têm nome abrem com as workspaces e unidades deles.
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

## Abrir

```sh
scripts/BlenderCraft.command
```

Também dá para dar dois cliques no arquivo pelo Finder. Ele abre o Blender com `--app-template blendercraft`. A extensão é habilitada pelo próprio template, e a aba **Assistente** aparece na barra lateral (tecla N) da 3D View.

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
