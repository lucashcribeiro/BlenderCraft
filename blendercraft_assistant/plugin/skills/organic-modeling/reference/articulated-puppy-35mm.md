# Cachorrinho articulado — medidas-alvo em 35 mm e lições

Extraído do dossiê do usuário (cachorrinho-medidas-completas.md): só as medidas-alvo e as lições das juntas. Eixos do dossiê: 0 = traseiro, 42 = nariz; alturas a partir do chão. É o exemplo canônico do formato de dossiê (ver blendercraft:quality-workflow).

## 8. Medidas-alvo para remodelar em 35 mm

Tudo em mm. Posição no eixo do comprimento: 0 = traseiro, 42 = nariz. Alturas a partir do chão.

**Conjunto:** altura 35 · comprimento 42 · dorso a 22,8 · barriga a 8,1.

**Tronco:** 32,8 de comprimento · 14,7 de altura · 17,0 de largura · dividido em 17,1 (traseira, de 0 a 17,1) + 15,7 (dianteira, de 17,1 a 32,8) · vão 1,3 · bola do meio Ø 7,0 / soquete Ø 7,4.

**Coxas (4, parte do tronco):** 10,0 de largura · descem do dorso até 11,0 do chão · 1,5 salientes para fora.

**Pernas (4):** Ø 8,0 · 13,4 do pivô à sola · 8,0 visíveis · pivô a 21,4 do chão · centros a 10,0 (trás) e 26,5 (frente) do traseiro · vão esquerda/direita 3,8 · de fora a fora 20,8 · bola Ø 5,0 / soquete Ø 5,4 (ou pino Ø 2,5 em furo Ø 3,0) · sola com raio 1,5 · almofadas em relevo de 0,6.

**Cabeça:** esfera Ø 14,8 · centro a 29,3 no eixo e 27,0 do chão · 17,0 topo→queixo · 19,7 nuca→nariz · rosto 16,5 · com orelhas 22,7 · topo 12,2 acima do dorso · pescoço opcional com bola Ø 6,0 / soquete Ø 6,4 a 30 no eixo.

**Focinho:** 5,0 de projeção · 6,4 de altura · 7,0 de largura · nariz 5,4 × 4,2 a 26 do chão.

**Orelhas:** 9,6 × 5,6 · 3,0 de espessura · começam 2,0 abaixo do topo, caídas.

**Olhos:** Ø 6,5 · centros a 9,8 um do outro · a 35,6 no eixo e 26 do chão · rebaixo de 0,4 para pintura ou troca de cor.

**Folgas (PLA, A1, camada 0,2):** 0,20 por lado em bolas e pinos para peças soltas; 0,30 para print-in-place; vão entre metades 1,3. Nunca escalar as folgas junto com o modelo.

**Outra altura:** multiplicar tudo (menos as folgas) por altura ÷ 35. 25 mm → × 0,71 · 50 mm → × 1,43 · 70 mm → × 2,0.

---

## Lições do modelo anterior (seções 5 a 7 e 9)

### Junta da perna (seção 5)

**Conclusão:** a folga foi colocada no pino, mas a bola e a haste encostam no corpo. Impresso como está, numa A1 com bico 0,4, as pernas saem **coladas** no corpo. Para funcionar precisa de 0,25 a 0,35 mm em todos os pares, e o pino pode ter menos (0,2).

Ainda: a malha é uma **união de cascas sobrepostas** (35 cascas, todas com volume positivo). Bola, furo e pino são sólidos separados ocupando o mesmo espaço. O fatiador funde tudo. Para a junta existir de verdade, o furo e o entalhe precisam ser subtraídos no CAD antes de exportar.

### Junta do meio do tronco (seção 6, como estava no modelo)

| Elemento | Medida |
|---|---:|
| Calota esférica presa ao tronco dianteiro | 4,86 larga × 9,85 alta × 4,07 de avanço, espessura 1,53 |
| Face côncava no tronco traseiro | casa com a calota |
| Pino transversal (eixo X) | 9,9 de ponta a ponta, Ø 3,1 no centro, lobos de 5,6 nas pontas |
| Lobos entram no tronco traseiro | 1,0 |
| Vão externo entre as metades | 0,78 |
| Folga calota × face côncava | **0,07** ❌ |

Mesmo problema: por fora tem vão de 0,78, mas por dentro a calota encosta. Solda na impressão.

Pata traseira esquerda com almofadas diferentes das outras três: erro de espelhamento (gerar o lado direito sempre por espelho real do esquerdo).

## 9. Resumo do que mudar no 2.0 CACHORRINHO

1. **Pernas:** alongar de 8,7 para 13,4 do pivô à sola (× 1,55) e engrossar para Ø 8. Subir o pivô para dentro do quadril (21 mm do chão). Afastar as pernas: vão esquerda/direita 3,8, fora a fora 20,8.
2. **Coxas:** criar a capa de quadril que esconde a junta.
3. **Folgas:** abrir 0,25–0,35 entre bola/haste e corpo, e entre calota e face côncava. Subtrair furos e entalhes de verdade no CAD (hoje são sólidos sobrepostos).
4. **Olhos:** Ø 3,3 → Ø 6,5 com rebaixo. **Nariz:** 3 × 2,5 → 5,4 × 4,2.
5. **Tronco:** mais alto (14,7) e mais largo (17,0); divisão no meio (17,1 + 15,7).
6. **Pata traseira esquerda:** conferir as almofadas, estão diferentes das outras três.
7. **Escala geral:** × 1,19 depois de corrigir as proporções, ou modelar direto nas medidas da seção 8.

## Nota de implementação (BlenderCraft, preset PUPPY_35 em articulated.py)

- "pivô a 21,4 do chão" e "13,4 do pivô à sola" não fecham (21,4 − 13,4 = 8,0 = parte visível). O preset usa o pivô a **13,4 do chão**, dentro do quadril e escondido pela coxa (coxa desce até 11,0), e a perna inteira do pivô à sola = 13,4.
- Junta da perna: pino Ø 2,5 preso no tronco, furo Ø 3,1 na perna (0,30 por lado, print-in-place); balanço ±20°.
- Junta do meio: bola Ø 7,0 no tronco dianteiro, soquete Ø 7,6 (0,30 por lado) no traseiro.
- Pescoço: bola Ø 6,0 / soquete Ø 6,6, gira ±25° em Z.
- Para peças soltas (montadas depois), usar clearance=0.20 (soquetes 7,4 / 6,4 e furo 2,9).
