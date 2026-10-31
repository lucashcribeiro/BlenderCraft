# Projeto executivo — Articulação de quadrúpedes a partir de STL do Meshy

**Versão:** 1.0 · 30/10/2026
**Aplica-se a:** qualquer quadrúpede (cão, gato, urso, dinossauro quadrúpede, cavalo, etc.) gerado no MakerWorld "Image to 3D" (Meshy 7 Ultra) e articulado no BlenderCraft para impressão *print-in-place*.
**Impressora de referência:** Bambu A1 · PLA · bico 0,4 mm · camada 0,2 mm.
**Base:** dossiê `cachorrinho-medidas-completas.md` (2.0 CACHORRINHO) e as referências estilo Wigglitz.

---

## 1. Objetivo e princípio central

Transformar um STL de peça única, vindo do Meshy, num brinquedo articulado que sai da impressora já montado. Ele tem cabeça e tronco, tronco que balança na cintura e 4 pernas que giram 90°: abertas para o lado na impressão e para baixo em pé.

**Princípio central: não quebrar o STL que veio.** A escultura do Meshy é o produto, então a articulação se adapta ao modelo, e não o contrário.

| Regra | O que significa na prática |
|---|---|
| Superfície externa intocada | Fora das zonas de corte, nenhum vértice muda: sem remesh, sem suavizar, sem decimar o modelo inteiro. |
| Cortes mínimos e escondidos | As únicas marcas visíveis são as linhas de separação (0,30 mm) e a abertura em U invertido sob cada coxa, onde a perna entra. |
| Juntas por dentro | Pinos, bolas, soquetes e bolsões ficam dentro do volume do bicho. |
| Original sempre guardado | Uma cópia oculta "STL original" fica na cena para comparação e para desfazer. |
| Adaptar, não impor | Medidas de junta saem das proporções do próprio modelo, dentro de limites mínimo e máximo, e as folgas são fixas. |
| Nada solda | Toda superfície que se move tem folga real recortada por booleana, medida depois do recorte. |

---

## 2. Entradas e preparação do STL

### 2.1 No Meshy / MakerWorld
- Gerar o bicho **em pé, de frente, com as 4 patas apoiadas e separadas**. Pernas coladas umas nas outras ou na barriga dificultam o corte.
- Pose neutra: cabeça pode virar até cerca de 20°, sem patas levantadas.
- Exportar em **STL** (geometria) ou **3MF** (geometria + cores). Resolução máxima (Ultra/4K): entre 500 mil e 1 milhão de faces é normal.

### 2.2 Importar no BlenderCraft
- **File › Abrir modelo…** (Cmd+Shift+O). O STL entra em mm.
- O BlenderCraft guarda uma cópia oculta chamada **"STL original"**.

### 2.3 Checagens (sem alterar a forma)
| Checagem | Ferramenta | Aceite | Se falhar |
|---|---|---|---|
| Escala | `inspect_object` | altura em pé = altura desejada (ex.: 35 mm) | escala uniforme no objeto inteiro; nunca deformar |
| Apoio | `analyze_figure` | as 4 patas tocam o chão (Z = 0) ± 0,1 mm | girar o modelo para nivelar; não esculpir |
| Malha fechada | `mesh_topology` | 1 componente, *manifold* | `prepare_mesh` com reparo **local**: fundir vértices a 0,001 mm, recalcular normais, fechar furos pequenos |
| Peças soltas | `mesh_topology` | sem ilhas flutuantes | apagar ilhas menores que 1 mm³; avisar se maiores |
| Faces | `inspect_object` | ≤ 1,2 milhão | acima disso, decimar **só para o cálculo das juntas**; a superfície final usa o original |

> **Proibido na preparação:** remesh voxel no modelo inteiro, suavização global ou decimação da superfície final. Se a booleana falhar por causa da malha, aplicar remesh apenas num **bloco local** em volta do corte (seção 13).

---

## 3. Marcos anatômicos (servem para qualquer quadrúpede)

Todos os marcos são detectados pela `analyze_figure` e mostrados como objetos auxiliares antes de cortar.

| Marco | Como detectar | Uso |
|---|---|---|
| **Chão** | menor Z do modelo nivelado | referência de todas as alturas |
| **Eixo do corpo (comprimento)** | maior dimensão horizontal do tronco (PCA da fatia entre barriga e dorso) | direção da cintura e dos pinos |
| **Plano de simetria** | meio da largura; conferir espelhando | espelhar juntas esquerda/direita |
| **Centro de cada pata** | fatia a 0,5–1,0 mm do chão → 4 ilhas | posição das pernas |
| **Linha da barriga** | subir fatias até as ilhas das pernas encostarem no tronco | altura onde a perna "entra" no corpo |
| **Dorso** | maior Z do tronco (sem cabeça) | altura do corpo |
| **Pivô de quadril/ombro** | acima do centro de cada pata, dentro da coxa: altura = barriga + 0,40 × (dorso − barriga), no meio da espessura da coxa | eixo do pino |
| **Cintura** | 52 % do comprimento do tronco, a partir do traseiro (dossiê: 17,1 de 32,8) | corte do meio |
| **Pescoço** | menor seção entre tronco e cabeça ao longo do eixo | junta de pescoço (opcional) |
| **Raiz do rabo** | protrusão mais traseira acima da barriga | rabo fixo ou junta opcional |

> **Nota do dossiê:** a altura do pivô de 21,4 mm não fecha com 13,4 mm do pivô à sola. O padrão usa **pivô a ≈ 0,38 × altura total** (13,3 mm em 35 mm), escondido sob a capa da coxa, que é o que reproduz a foto: perna visível de cerca de 8 mm abaixo da barriga.

---

## 4. Divisão do corpo em peças

| Peça | Origem no STL | Observação |
|---|---|---|
| **Frente** (cabeça + tronco dianteiro) | tudo à frente do plano da cintura, menos as pernas dianteiras | a cabeça pode ser uma peça separada se houver junta de pescoço |
| **Traseira** (tronco traseiro + rabo) | tudo atrás da cintura, menos as pernas traseiras | rabo fixo por padrão |
| **4 pernas** | coluna de cada pata, da sola até o pivô, mais o "nó" superior que entra no bolsão | separadas do corpo pela superfície do bolsão |
| **Inserções de cor** (opcional) | olhos, nariz, almofadas | só se o modelo trouxer cores (3MF) ou o usuário pedir |

### Regras de corte
1. **Cintura:** superfície perpendicular ao eixo do corpo, levemente curva para seguir a dobra natural do pelo. O vão visível é de 1,3 mm (padrão do dossiê; mínimo 0,8 mm).
2. **Perna × corpo:** a perna não é "serrada" na horizontal. O corpo recebe um **bolsão** (o volume varrido pela perna de 0° a 90°, mais a folga) e a parte da perna que fica dentro dele vira o **nó** arredondado.
3. **Visível de fora:** apenas o vão da cintura, a linha da perna saindo sob a coxa e a abertura em U invertido na lateral da coxa (igual às referências Wigglitz).
4. **Esconder:** quando houver pelo, dobra ou mancha de cor perto do corte, o corte segue essa linha (até ±2 mm do marco).

---

## 5. Junta padrão da perna

### 5.1 Como funciona
- **Tipo:** dobradiça com **pino transversal** (eixo paralelo ao comprimento do corpo, Y). A perna gira de 0° (aberta para o lado, deitada na mesa de impressão) até 90° (para baixo, em pé).
- **Pino:** faz parte do **corpo** e atravessa o bolsão de parede a parede. A perna tem um **furo** no nó.
- **Bolsão:** cavidade dentro do quadril/ombro com **abertura em U invertido** voltada para fora e para baixo. A capa da coxa (casca externa do corpo) cobre a junta.
- **Batentes:**
  - em **90°**, a face traseira do nó encosta no teto do bolsão, e a perna para em pé, firme;
  - em **0°**, a perna para na horizontal, que é a posição de impressão.

### 5.2 Medidas no tamanho de referência (altura total H = 35 mm)
| Elemento | Medida | Relativo a H | Limites |
|---|---|---|---|
| Diâmetro do pino | 2,5 mm | 0,071 H | mín. 2,0 · máx. 4,0 |
| Furo no nó da perna | pino + 0,60 = 3,1 mm | — (folga fixa) | — |
| Largura da perna (haste) | 8,0 mm | 0,23 H | mín. pino + 2 × 1,2 + 0,6 ≈ 5,5 |
| Raio do nó (em volta do pino) | metade da largura da perna = 4,0 mm | — | — |
| Pino embutido em cada parede | ≥ 1,5 mm | — | fixo |
| Parede da capa da coxa | ≥ 1,2 mm | — | fixo (3 perímetros de 0,4) |
| Bolsão | volume varrido do nó de 0° a 90° + 0,30 | — | folga fixa |
| Abertura em U (largura) | largura da perna + 2 × 0,30 = 8,6 mm | — | — |
| Pivô → sola | 13,4 mm | 0,38 H | — |
| Altura do pivô | 13,3 mm do chão | 0,38 H | dentro da capa da coxa |
| Capa da coxa: desce até | 11,0 mm do chão | 0,31 H | — |
| Capa da coxa: saliência | 1,5 mm para fora | 0,043 H | — |
| Vão esquerda/direita entre pernas | 3,8 mm | 0,11 H | mín. 2,0 |
| Pernas de fora a fora | 20,8 mm | 0,59 H | — |

### 5.3 Como construir (ordem)
1. Criar o **eixo do pino** em cada pivô (marco da seção 3), paralelo ao eixo do corpo.
2. Separar a **perna**: pegar a coluna da pata e fechar o topo com o **nó cilíndrico**, de raio igual a metade da largura e centrado no eixo do pino.
3. **Varrer** a perna de 0° a 90° em torno do eixo, unir os volumes, aplicar offset de +0,30 mm e **subtrair do corpo** (booleana EXACT). Isso gera o bolsão e a abertura em U.
4. Garantir a **capa da coxa**: medir a parede restante; se ficar abaixo de 1,2 mm, engrossar só por dentro, nunca por fora.
5. Criar o **pino** (Ø 2,5) unido ao corpo, atravessando o bolsão e embutido no mínimo 1,5 mm em cada parede.
6. Furar o **nó** (Ø 3,1) com booleana EXACT.
7. **Batentes:** o teto do bolsão tangencia o nó a 90° com 0,30 de folga, e o atrito leve segura a perna em pé.
8. **Medir** cada par (pino × furo, nó × bolsão, haste × abertura, perna × perna vizinha) e fazer a **varredura de 0° a 90°** em passos de 5°.

### 5.4 Folgas da perna (impressão já montada)
| Par | Folga por lado |
|---|---|
| Pino × furo | 0,30 |
| Nó × paredes do bolsão | 0,30 |
| Nó × teto do bolsão | 0,30 |
| Haste × abertura em U | 0,30 |
| Perna × corpo no plano da mesa | ≥ 0,30 (no XY, na primeira camada) |

---

## 6. Juntas do tronco, pescoço e rabo

### 6.1 Cintura (o "balanço" do corpo)
| Elemento | Medida em 35 mm | Relativo | Folga |
|---|---|---|---|
| Bola presa à metade dianteira | Ø 7,0 | 0,20 H | — |
| Soquete na metade traseira | Ø 7,6 | bola + 0,60 | 0,30 por lado |
| Vão visível entre as metades | 1,3 | 0,037 H | mín. 0,8 |
| Pescoço da bola (haste) | Ø 4,0 | 0,11 H | mín. 3,0 |
| Boca do soquete (retenção) | Ø 5,2 | maior que a haste + 0,6 e menor que a bola | segura a bola sem sair |
| Giro livre esperado | ±30° de torção · ±10° lateral | — | — |

Alternativa do 2.0: **calota esférica + pino transversal**. Só deve ser usada se o tronco for estreito demais para a bola (largura do tronco < bola + 2 × 1,2 + 0,6).

### 6.2 Pescoço (opcional)
- Bola Ø 6,0 (0,17 H) na cabeça e soquete Ø 6,6 no tronco dianteiro, na menor seção do pescoço.
- Usar só se o pescoço tiver pelo menos 9 mm de diâmetro em 35 mm. Caso contrário, a cabeça fica fixa.

### 6.3 Rabo
- Fixo por padrão, colado à traseira.
- Junta de rabo (bola Ø 3,5 / soquete Ø 4,1) só se o rabo tiver ≥ 5 mm de espessura na raiz.

---

## 7. Tabela paramétrica (qualquer tamanho)

**Regra:** `medida = valor_35mm × (H ÷ 35)`, respeitando o mínimo e o máximo. **Folgas e paredes nunca escalam.**

| Grupo | Item | Em 35 mm | Mín. | Máx. | Escala? |
|---|---|---|---|---|---|
| Folgas | Print-in-place | 0,30 | — | — | **não** |
| Folgas | Montado depois | 0,20 | — | — | **não** |
| Folgas | Vão da cintura | 1,3 | 0,8 | 2,0 | sim (limitado) |
| Paredes | Mínima | 1,2 | 1,2 | — | **não** |
| Paredes | Pino embutido | 1,5 | 1,5 | — | **não** |
| Perna | Pino | 2,5 | 2,0 | 4,0 | sim |
| Perna | Furo | pino + 0,6 | — | — | — |
| Perna | Largura da haste | 8,0 | 5,5 | — | sim (só se o modelo já tiver) |
| Perna | Pivô → sola | 13,4 | — | — | sim |
| Cintura | Bola | 7,0 | 4,5 | 12 | sim |
| Cintura | Soquete | bola + 0,6 | — | — | — |
| Pescoço | Bola | 6,0 | 4,0 | 10 | sim |
| Detalhes | Menor detalhe impresso | 0,4 | 0,4 | — | **não** |

Exemplos de fator: 25 mm → × 0,71 · 50 mm → × 1,43 · 70 mm → × 2,0.

> Em modelos pequenos (≤ 25 mm), os mínimos dominam. Se a perna do Meshy for mais fina que 5,5 mm, ver a seção 13 (aumentar a escala do modelo inteiro ou trocar para "nó interno" maior que a perna, escondido na coxa).

---

## 8. Regras de impressão (Bambu A1, PLA, 0,4, camada 0,2)

| Regra | Valor |
|---|---|
| Posição de impressão | tronco apoiado na barriga e pernas abertas a 0° (deitadas para o lado), como nas referências |
| Folga em qualquer par móvel | 0,30 por lado |
| Folga vertical entre peças sobrepostas | ≥ 0,30, o que dá uma camada + margem |
| Primeira camada | peças diferentes nunca se tocam no XY: ≥ 0,30 de vão no plano da mesa |
| Balanço máximo sem suporte | 45° a partir da vertical |
| Ponte máxima (teto do bolsão) | 8 mm; acima disso, arredondar o teto em arco |
| Parede mínima | 1,2 mm (3 perímetros) |
| Menor detalhe | 0,4 mm de largura · 0,2 mm de profundidade |
| Suportes | **nenhum** dentro das juntas; se precisar, só na parte externa, longe das folgas |
| Depois de imprimir | girar cada perna devagar até soltar ("quebrar a primeira camada") |

---

## 9. Cores e peças multicolor

- Cada cor vira uma **peça separada** no 3MF, encaixada na peça principal (olhos, nariz, almofadas, manchas). Com isso o Bambu Studio atribui um filamento por peça.
- Inserções de cor ficam **rebaixadas 0,4 mm** na peça principal, ou colocadas em cima com 0,6 mm de relevo (almofadas).
- **Espelhar sempre esquerda e direita:** no 2.0, a pata traseira esquerda tinha almofadas diferentes. A checagem de espelho é obrigatória.
- Se o 3MF do Meshy vier com cores pintadas, a cor de cada região é aproveitada como peça. Caso contrário, separar por seleção.

---

## 10. Procedimento no BlenderCraft (passo a passo)

Em cada etapa a IA mostra prints e uma linha de progresso em português. **Antes de cortar, ela pede confirmação.**

| # | Etapa | Ferramentas | Prompt pronto para colar no chat |
|---|---|---|---|
| 1 | Importar | Abrir modelo… | (menu: File › Abrir modelo…) |
| 2 | Preparar sem estragar | `prepare_mesh`, `mesh_topology` | "Prepara esse STL para articular sem mudar a superfície: só reparo local, guarda o original oculto e me diz faces, se é manifold e as medidas." |
| 3 | Escala | `inspect_object` | "Deixa ele com 35 mm de altura em pé, escala uniforme." |
| 4 | Marcos | `analyze_figure` | "Analisa a figura e mostra os marcos: chão, patas, barriga, dorso, pivôs das 4 pernas, cintura, pescoço e rabo. Desenha os planos e eixos e tira prints de frente, lado e de baixo." |
| 5 | Plano | — | "Monta o plano de articulação pelo projeto executivo (perna com pino transversal e bolsão em U, cintura com bola) e me mostra a tabela de medidas antes de cortar." |
| 6 | Cortar | `cut_part` | "Pode cortar: separa as 4 pernas e divide o tronco na cintura." |
| 7 | Juntas | `add_joint`, `fit_clearance`, `boolean_parts` | "Cria as juntas: pino Ø2,5, furo Ø3,1, bolsão varrido de 0 a 90° com 0,30 de folga e bola da cintura Ø7,0 com soquete Ø7,6." |
| 8 | Medir | `measure`, `joint_test` | "Mede todas as folgas e faz a varredura de 0 a 90° em cada perna. Me dá a tabela." |
| 9 | Impressão | `print_check`, `mesh_topology` | "Checa paredes, balanços, primeira camada e se cada peça é uma casca fechada." |
| 10 | Pose de impressão | `joint_set_angle` | "Coloca as pernas a 0° (abertas para o lado) e apoia o tronco na mesa." |
| 11 | Exportar | `export_file` | "Exporta 3MF com todas as peças separadas e montadas, em mm." |
| 12 | Conferir | — | "Compara a superfície final com o STL original fora das zonas de corte e me diz se algo mudou." |

---

## 11. Verificação e critérios de aceite

| Critério | Como medir | Aceite |
|---|---|---|
| Folga de cada par móvel | `measure` (distância mínima) | ≥ 0,28 mm (alvo 0,30) |
| Sem sobreposição | distância mínima > 0 em todos os pares | sim |
| Giro das pernas | varredura de 0 a 90° em 5° | sem colisão em todo o curso |
| Giro da cintura | varredura de ±30° | sem colisão |
| Cada peça | `mesh_topology` | 1 casca, manifold, normais corretas |
| Paredes | `print_check` | ≥ 1,2 mm em volta de pino e soquete |
| Superfície preservada | vértices fora das zonas de corte vs STL original | 100 % idênticos |
| Medidas gerais | altura, comprimento e largura em pé vs original | diferença ≤ 0,05 mm (exceto a escala pedida) |
| Espelho E/D | volumes e almofadas das pernas | iguais (± 0,5 %) |
| Pose de impressão | peças no plano da mesa | vão XY ≥ 0,30 entre peças |

---

## 12. Adaptação por espécie

| Espécie | Diferenças | Ajustes |
|---|---|---|
| **Cão** (padrão) | pernas curtas e grossas, tronco em barril | tudo como neste documento |
| **Gato** | pernas mais finas e longas, rabo comprido | pino no mínimo de 2,0; rabo com junta (bola Ø 3,5) se tiver ≥ 5 mm na raiz; cintura mais alta |
| **Urso** | pernas muito grossas, tronco largo | pino de 3,0; bola da cintura no máximo proporcional; coxas grandes facilitam esconder o bolsão |
| **Dinossauro quadrúpede** (triceratops, estegossauro) | rabo longo, placas e chifres | rabo em 2 ou 3 segmentos com bolas; placas e chifres nunca cortados, ficam inteiros numa peça |
| **Cavalo / cervo** | pernas longas e finas | se a perna tiver < 5,5 mm, aumentar a escala do modelo ou usar um nó interno maior escondido no corpo; considerar só a junta do ombro, sem joelho |
| **Elefante / rinoceronte** | pernas em coluna, cabeça pesada | bola de pescoço reforçada (Ø 0,20 H) ou cabeça fixa |
| **Tartaruga** | casco cobre as pernas | bolsão sob o casco; pernas curtas com pino vertical ou transversal conforme a pose |

---

## 13. Riscos e soluções

| Risco | Sintoma | Solução |
|---|---|---|
| Malha do Meshy não manifold | booleana falha ou cria buracos | reparo local; se persistir, remesh **só num bloco** em volta do corte, unindo depois sem tocar no resto |
| Pernas coladas na barriga ou entre si | não há espaço para o bolsão | cortar pelo plano de separação natural e criar o bolsão; avisar que a pata vai perder um pedaço oculto |
| Perna fina demais | parede < 1,2 em volta do furo | aumentar a escala, pino mínimo de 2,0 ou nó interno maior que a perna dentro da coxa |
| Pose torta (cabeça virada, pata à frente) | marcos assimétricos | pivôs calculados por perna, não espelhados; conferir a varredura de cada uma |
| Pelo ou detalhe sobre a linha de corte | detalhe partido | mover o corte até ±2 mm para seguir a dobra; nunca cortar olhos ou orelhas |
| Folga que solda | peças coladas depois de imprimir | medir depois de cada booleana; nunca aceitar menos de 0,28 |
| Folga escalada junto com o modelo | encaixe frouxo ou soldado | folgas e paredes são valores fixos (seção 7) |
| Chamada longa demais da IA | ação cancelada | trabalhar em etapas curtas (uma junta por vez) |
| Peça com várias cascas sobrepostas (problema do 2.0) | o fatiador funde tudo | sempre recortar de verdade (booleana EXACT) e checar com `mesh_topology` |

---

## 14. Anexo — referência do cachorrinho 2.0 (35 mm)

Do dossiê `cachorrinho-medidas-completas.md`, seção 8 (alvos):

- **Conjunto:** altura 35 · comprimento 42 · dorso a 22,8 · barriga a 8,1.
- **Tronco:** 32,8 × 14,7 × 17,0; dividido em 17,1 (traseira) + 15,7 (dianteira); vão 1,3; bola do meio Ø 7,0 / soquete Ø 7,4 (neste projeto, 7,6 para 0,30 de folga).
- **Coxas:** 10,0 de largura; descem até 11,0 do chão; saliência de 1,5.
- **Pernas:** Ø 8,0; 13,4 do pivô à sola; centros a 10,0 (trás) e 26,5 (frente) do traseiro; vão esquerda/direita 3,8; fora a fora 20,8; pino Ø 2,5 em furo Ø 3,1.
- **Cabeça:** esfera Ø 14,8; 17,0 do topo ao queixo; 19,7 da nuca ao nariz; rosto 16,5; 22,7 com orelhas.
- **Focinho:** 5,0 × 6,4 × 7,0; nariz 5,4 × 4,2.
- **Orelhas:** 9,6 × 5,6 × 3,0, caídas.
- **Olhos:** Ø 6,5; 9,8 entre centros; rebaixo de 0,4.

**Defeitos do 2.0 que este padrão corrige:**
- folgas de 0,06 a 0,18 mm que soldam, trocadas por 0,30 em tudo;
- furos e bolas como sólidos sobrepostos, trocados por recortes de verdade;
- 35 cascas unidas, trocadas por 1 casca por peça;
- almofadas espelhadas erradas, resolvidas com checagem de espelho obrigatória;
- perna curta com pivô baixo, trocada por pivô dentro da coxa, a 0,38 H.
