# Revisão de engenharia — padrão da perna e do recorte (extrato)

Fonte: revisão Dog-Padrao-V1 (relatorio.md, seções 6 e 7.1). Este extrato substitui o projeto executivo onde for diferente.

## 6. Padrão proposto (35 mm) — perna "Wigglitz" + recorte em arco

Este é o mesmo princípio das fotos 21–27: a perna imprime deitada para o lado e gira 90° para baixo num pino transversal (eixo Y).
- De baixo, o corpo mostra uma **cruz** entre os 4 bolsos.
- Do lado, a perna fica num **arco em U invertido** (topo plano com chanfros de 45°). A capa da coxa esconde a junta.

### 6.1 Números
| Item | Valor (35 mm) | Regra |
|---|---|---|
| Perna: seção | **8,0 (Y) × 8,0 (X)**, chanfros 45° de **2,0** nas 4 arestas longas | W = T = 0,229·H |
| Perna: pivô → sola | **12,1** | = barriga + T/2 |
| Topo da perna (atrás do pino) | **sólido de revolução** da seção em volta do eixo do pino (Spin 180°), raio 4,0 = T/2 | gira dentro de si mesmo; folga constante |
| Furo na perna | **Ø3,10** (2,5 + 2×0,30), em **gota** com ponta de 45° para cima (na impressão) | furo = pino + 0,60 |
| Pino | **Ø2,50** com **base plana** (corte de 0,20 embaixo), comprimento **11,6** = 8,0 + 2×0,30 + 2×1,5; fundido ao corpo | Ø ≥ 2,0 sempre |
| Embutimento do pino | **1,5** por lado; **≥ 1,2** de parede em volta e além da ponta | não escala abaixo de 1,2 |
| Pivô | **4,0 acima da barriga** na impressão = **12,1 do chão** em pé; **X = ±5,9** do centro | pivô = T/2 |
| Posição em Y (do traseiro) | traseira **9,5**, dianteira **25,5** (alvo 10,0 / 26,5, recuado para manter ≥1,2 de parede além do pino) | parede ≥ 1,2 |
| Bolso (recorte) | **varredura da perna inflada 0,30 de 0° a 90°** = Spin 270° da seção inflada (8,6 × 8,6, chanfro 2,18) ∪ hastes infladas a cada 5° | folga 0,30 fixa |
| Arco lateral (vista de lado) | largura **8,6**, topo plano **4,24**, chanfros 45° de **2,18**, altura **8,3** acima da barriga (topo a **16,4** do chão em pé) | = T + 0,30 |
| Teto interno | raio **4,30** em volta do pino; cobertura acima ≥ 2 (no protótipo, 6,4) | — |
| Espinha (barra longitudinal da cruz) | **3,2** (= 2·(5,9 − 4,3)) | ≥ 2,4 |
| Barra transversal da cruz | ≥ 6 entre os bolsos dianteiro e traseiro (7,4 no protótipo) | — |
| Vão E/D das pernas / fora a fora | 3,8 / 19,8 (20,8 com a pata 0,5 mais larga para fora) | — |
| Batentes | **+94°** (perna contra a espinha, trava em pé) e **−4°** (contra o arco) | automáticos pela geometria |

**Verificado no protótipo** (`proposta_padrao_prototipo.blend`, mesmas rotinas BVH):
- Perna × corpo = **0,297–0,30 em todos os ângulos de 0 a 90°** (passo de 5°), nas 4 pernas.
- Pino × furo: 0,30 nos lados, 0,34 na base e no topo.
- Contato a +95° e −5°.
- Primeira camada: 1,9 de afastamento (graças ao chanfro de baixo).
- Pino: 1,5 dentro da parede, com 1,39 de parede além da ponta (dianteira) e 3,6 (traseira).

**Variante A ("igual à foto de lado"):** use T = 6,0 (X) e W = 8,0 (Y).
- Pivô 3,0 acima da barriga (11,1 do chão); arco 6,3 acima da barriga (topo a 14,4, foto 14,2); topo da perna 14,1 (foto 13,9).
- Perde espessura de frente (6,0 em vez de 8,5).
- Parede em volta do furo dentro da perna = 3,0 − 1,55 = 1,45 (ok).

### 6.2 Receita passo a passo (Blender / IA do BlenderCraft)
1. **Pose de impressão:** pivôs a 0°, `BC_Cachorrinho` em Z = 0. Todos os booleans são feitos nessa pose.
2. **Seção da perna** (plano YZ local, eixo do pino na origem):
   - Octógono 8,0 × 8,0 com chanfros de 45° de 2,0: pontos (±2,0, ±4,0) e (±4,0, ±2,0).
   - Extrudar em +X (para fora) de −0,02 até 12,1 → **haste**.
3. **Topo:** com a mesma seção, Spin de 180° em volta do eixo Y do pino, do −Z ao +Z passando por −X → **ponta de revolução**. Unir à haste (Exact).
4. **Furo:** gota Ø3,10 com ponta para +Z, atravessando 8,0 em Y. Boolean de diferença na perna.
5. **Cortador do bolso:**
   - Seção inflada: 8,6 × 8,6, chanfro 2,18.
   - (a) Spin de **270°** da seção inflada, de −180° a +90° (cobre a ponta em todas as posições).
   - (b) Haste inflada (−0,02 até 12,4 + 6 de sobra para fora) duplicada e girada no eixo do pino a **0, 5, 10 … 90°**.
   - Juntar tudo numa coleção `cut_<perna>` e fazer **Diferença (Exact, operando = coleção)** no corpo.
   - Não use cópias da ponta girada: superfícies quase coincidentes quebram o Exact. Por isso a ponta entra como Spin.
6. **Pino:** cilindro Ø2,50 (base plana a −1,05 do eixo), comprimento 11,6 centrado no bolso. **Unir ao corpo** depois do recorte.
7. **Espelhar** para x− e repetir na traseira. Conferir: parede ≥ 1,2 além da ponta do pino; espinha ≥ 2,4.
8. **Verificar** com a varredura 0→90° (passo de 5°). Tudo deve dar ≥ 0,29 e sem sobreposição em 0°.
9. **Capa da coxa (estético):** saliência de 1,5 no flanco em volta do arco. Arredondar a aresta externa do arco em 0,3–0,4 (não altera folga). Opcional, como na foto: alívio chanfrado de 1,5 × 45° na borda do lado da cintura.
10. **Folgas verticais:** nas faces horizontais (teto do bolso, base do pino), deixe a cota em múltiplo de 0,2. Ex.: teto do bolso a z = 8,4 em vez de 8,3.

### 6.3 Escala (folgas nunca escalam)
| Altura | Perna T=W | pivô→sola | chanfro | pino / furo | embut. | pivô X | espinha | bolso Y |
|---|---|---|---|---|---|---|---|---|
| 25 mm | 5,7 | 8,6 | 1,4 | **2,0 / 2,6** (mín.) | 1,2 | **4,4** (para espinha 2,4) | 2,4 | 6,3 |
| 35 mm | 8,0 | 12,1 | 2,0 | 2,5 / 3,1 | 1,5 | 5,9 | 3,2 | 8,6 |
| 50 mm | 11,4 | 17,3 | 2,9 | 3,6 / 4,2 | 2,1 | 8,4 | 4,8 | 12,0 |
| 70 mm | 16,0 | 24,2 | 4,0 | 5,0 / 5,6 | 3,0 | 11,8 | 7,0 | 16,6 |

Ficam fixos em qualquer tamanho: folga radial 0,30 (print-in-place) ou 0,20 (montado), folga vertical 0,4, parede ≥ 1,2, espinha ≥ 2,4, pino ≥ Ø2,0.


### 7.1 Recorte do corpo para a pata
Detalhes da abertura no corpo (recortes 39/40):
- **Formato:** U invertido de topo plano. Os cantos são chanfros/raios de ~2,2 mm, praticamente iguais nas 4 aberturas.
- **Largura** de borda a borda 10,35–10,40. Largura "livre" ao lado da perna: 8,5–8,9.
- **Altura do chão ao topo:** 14,17–14,22.
- **Raio/chanfro do canto:** ~2,0–2,3.
- **Espessura da capa:** não dá para medir de lado. A aresta aparece como um friso claro de 3–6 px (0,15–0,3 mm), ou seja, aresta levemente arredondada.
- **Faixa interna do lado da cintura:** 1,7–1,8 de superfície em sombra, um chanfro/parede interna do recorte.
- **Linhas de folga verticais:** 0,31–0,36 do lado de fora de cada perna.
- **Entalhe em V na cintura:** ápice a 10,8 do chão; altura 3,2; largura de 1,1 a y = 520 e de 1,6 a y = 540 (os dois troncos têm o canto inferior com raio de ~3 na face da cintura).
- **Borda inferior de cada metade:** 7,6–8,6.

Especificação proposta do recorte (35 mm, perna 8×8). Desenhos: `42_proposta_recorte_lateral_cotado.png` (lado), `43…XZ` (frente/corte perpendicular ao pino), `44…YZ`, `45…XY`.

| Cota | Valor | Fórmula |
|---|---|---|
| Largura da abertura | **8,60** | W + 2×0,30 |
| Altura do arco (barriga → topo) | **8,30** | T + 0,30 |
| Topo plano | **4,24** | (W + 0,6) − 2×chanfro |
| Chanfro dos cantos | **45° × 2,18** | 2,0 + 0,30·(2−√2) |
| Profundidade (flanco → espinha) | **8,4** (x de 1,6 a 10,0) | pivô X + T/2 + 0,30 + capa |
| Teto interno (em volta do pino) | **R 4,30** de revolução | T/2 + 0,30 |
| Folga do teto (topo da perna × arco) | **0,30** (no plano do pino) | fixa |
| Aresta externa do arco | arredondar 0,3–0,4 | só estético |
| Alívio do lado da cintura (opcional, como na foto) | 1,5 × 45° | não reduz a folga |
| Parede mínima (em volta do pino e além da ponta) | **1,2** | fixa |
| Espinha entre bolsos | **3,2** (mín. 2,4) | 2·(pivô X − T/2 − 0,30) |
| Entalhe da cintura | canto inferior R 3 em cada metade; vão 1,3 | — |

