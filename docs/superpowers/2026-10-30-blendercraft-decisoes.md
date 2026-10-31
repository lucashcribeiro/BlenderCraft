# BlenderCraft v1 — Decisões tomadas na execução e checklist de teste

Durante a execução do plano, várias decisões foram tomadas sem consultar o usuário. Esta lista reúne todas elas, na ordem em que foram tomadas, cada uma com o custo caso esteja errada. Revise e diga o que mudar.

## Decisões

### Estrutura e processo
1. `__init__.py` do pacote e `tools/__init__.py` não importam `bpy` no topo (as ferramentas são carregadas por `load_all()`). Custo se errado: pequeno refactor.
2. `AgentSession` recebe os textos de aviso por um parâmetro `notes`, em pt/en. Custo: nenhum.
3. Commits sem coautor de IA, push para o GitHub e commits atômicos, como você pediu. Custo: nenhum.
4. O executor de testes aceita um único `-k`. Custo: nenhum.

### Unidades e exportação
5. `format_length` converte à mão para mm, cm, m, polegadas e pés, porque o `to_string` do Blender 5.2 ignora a unidade escolhida. `mm_per_bu` arredonda o float32. Custo: nenhum relevante.
6. O glTF sai em metros, como o formato exige: a exportação usa um nó raiz com escala, e a importação usa a conversão do próprio Blender. Custo: um nó raiz extra no arquivo.
7. A cor de cada peça no 3MF vem do Base Color do Principled BSDF. Se não houver, vem da cor do viewport. Custo: nenhum.
8. O ângulo de balanço do `print_check` segue a convenção dos fatiadores, medido a partir da vertical. Em 45° o resultado não muda. Custo: nenhum em 45°.
9. A distância entre peças (`measure`) é exata quando a malha é pequena. Em malhas grandes ela é aproximada e vem marcada como `approximate`. Custo: lentidão em malhas médias.
10. Toda mensagem enviada ao Claude leva uma linha de contexto com a unidade exibida, quantos mm vale 1 unidade naquele arquivo e o idioma. Assim, um `.blend` feito em metros não gera peças 1000 vezes maiores, e trocar a unidade no meio da conversa funciona. Custo: algumas dezenas de tokens por mensagem.
11. Caminhos relativos em importar, exportar e salvar resolvem a partir da pasta do `.blend` aberto. Sem arquivo aberto, resolvem a partir de `~/Documents/BlenderCraft`. A resposta sempre traz o caminho absoluto. Custo: nenhum.
12. Na cena nova, o template coloca luz e câmera na escala de mm, deixa a vista inicial a 80 mm e ajusta o alcance da câmera. Custo: nenhum.

### Desfazer
13. Qualquer mudança feita por você depois de um pedido, até uma seleção, bloqueia o botão "Desfazer mudanças da IA". Quando isso acontece, ele abre o Undo History. Custo: o botão bloqueia mais vezes.
14. Antes de sair do escopo da IA, a cena é atualizada, para que a mudança da IA não seja confundida com uma edição sua. Custo: nenhum (conferido no Blender com janela).
15. Se uma ação demorar mais de 120 s, o tempo esgota, mas ela ainda pode ter sido aplicada. A IA é avisada pra inspecionar a cena. Custo: a IA pode repetir uma ação.
16. O desfazer usado pela própria IA atualiza a contagem de passos dela e não pode voltar além do pedido atual. Custo: nenhum.
17. O print anexado à mensagem não conta como edição sua. Custo: nenhum.

### Claude embutido e segurança
18. O Claude do BlenderCraft não carrega os seus hooks, plugins nem configurações pessoais (`--setting-sources ""`). O login continua funcionando. Custo: nenhum observado.
19. Das ferramentas embutidas do Claude, só a "Skill" fica ligada, pra que ele consiga usar as skills de designer. Ela só lê o conteúdo das skills, sem acesso a arquivos nem à internet. Custo: nenhum.
20. O token do MCP fica num arquivo temporário com permissão 0600, e não na linha de comando. Custo: um arquivo temporário a limpar.
21. Ao iniciar o Claude, o app remove só as variáveis de ambiente que o prendem a uma sessão-pai do Claude Code. As de autenticação e proxy ficam. Custo: só afeta quem desenvolve dentro de um terminal do Claude.
22. Os arquivos de teste gravados não contêm dados da sua conta, como uso e créditos, nem no histórico do git. Custo: nenhum.
23. O servidor recusa ferramentas que não estejam liberadas no modo atual, então o "Só leitura" é garantido no código.
24. O modo "Perguntar antes de alterar" também é imposto pelo servidor, com uma permissão de uso único por aprovação. Custo: nenhum observado.
25. A ferramenta de aprovação só aparece no modo "Perguntar". Custo: nenhum.

### Conversa e fila
26. A saída de um processo do Claude já encerrado é descartada, pra não misturar com a resposta nova. Custo: nenhum.
27. Sobre a fila de mensagens:
    - "Parar" limpa a fila.
    - O limite de passos e o "Enviar agora" mantêm a fila.
    - Num erro, a fila é descartada e o painel avisa quantas mensagens foram perdidas.

    Custo: UX levemente diferente.
28. Se a conversa não puder ser retomada, o app só recomeça com um resumo quando o turno retomado falhou sem ter feito nada. Nos outros casos ele mostra o erro e mantém a memória. Custo: uma sessão realmente quebrada exige "Nova conversa".
29. O botão "Copiar comando MCP" fica no painel e nas preferências, e o seletor de unidade fica no cabeçalho da 3D View. O token muda a cada vez que o Blender abre, então o comando precisa ser copiado de novo depois de reiniciar. Custo: recopiar o comando.

### Pendentes, levados a você
30. **Enter não envia mais a mensagem; só o botão ▶ envia.** O Blender não deixa distinguir Enter de "clicou fora do campo", e enviar ao clicar fora mandava mensagens sem o anexo. Pode ser que seja preciso clicar duas vezes no ▶ enquanto você ainda está digitando. **Decida depois de testar:** dá pra trocar por uma janela de mensagem que envia com Enter.
31. Ficam aceitos para a v1, por serem casos raros:
    - O desfazer da IA não verifica uma edição sua feita no meio do pedido.
    - Um erro no fim de um turno retomado que já tinha texto não aparece.
    - Pequenas janelas de corrida nas aprovações logo depois de "Parar".

## Checklist de teste manual (precisa da janela do Blender)

**Instalação**
1. Rode `scripts/install.sh` (ou `scripts/install.sh --dev`, que cria links para o repo) e abra `scripts/BlenderCraft.command`. O Claude Code precisa estar instalado e com login feito (rode `claude` uma vez).
2. Em Preferences › Extensions, a extensão aparece ligada.
3. Não há aba Animation nem timeline. A barra lateral abre com a aba Assistente. O cubo tem 20 mm e a unidade é mm.
4. Nas preferências da extensão há o caminho do Claude Code, a porta e o botão "Copiar comando MCP".

**Conversa e ferramentas**

5. Peça: "crie um cubo de 20 mm com furo de 5 mm e exporte 3MF em ~/Desktop/teste.3mf". Confira três coisas:
   - O texto chega aos poucos.
   - As ferramentas aparecem com "Detalhes".
   - O arquivo é criado.

   Com o Blender em pt_BR, o painel fica em português.
6. Clique em "Parar" no meio de um pedido longo. A interface não pode travar, e as ferramentas em andamento aparecem como interrompidas.
7. Mande uma segunda mensagem enquanto a IA trabalha (pelo botão ▶) e confira o "1 na fila". "Enviar agora" interrompe e envia na hora.
8. Envie uma mensagem com "Anexar vista" e "Anexar seleção" ligados.
9. Troque a unidade para polegadas. As medidas exibidas mudam e a IA responde em polegadas, mas o STL/3MF continua saindo com 20 mm.
10. Abra um `.blend` comum, feito em metros. O painel avisa sobre a escala, e as peças criadas e exportadas saem no tamanho certo.

**Permissões e desfazer**

11. No modo "Perguntar", clique em **Recusar** e confira se o Claude informa a recusa.
12. Teste "Desfazer mudanças da IA". Depois edite algo à mão e clique de novo: aparece um aviso e abre o Undo History.

**Articulação**

13. No subpainel Articulação: crie o pivô com a peça selecionada, gire pelo slider, use "Testar" pra ver a faixa livre e depois "Voltar à posição".

**Conversas e limpeza**

14. No popover de Conversas, abra uma conversa antiga e continue. Ao apagar uma, aparece uma confirmação.
15. Feche e reabra o Blender e continue a conversa pela lista.
16. Confira que nenhum `claude` ficou rodando depois de fechar o Blender: `ps aux | grep -i "[c]laude -p"`.
