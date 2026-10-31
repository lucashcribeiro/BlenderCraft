## 4. Pernas (4 iguais)

**Situação atual:** as pernas têm 18 mm de seção e ficaram fracas na simulação.

| Perna | Seção (mm) | Altura (mm) | Material | Carga (N) | Margem | Status |
|---|---|---|---|---|---|---|
| Frontal esq. | 18 × 18 | 420 | PLA | 350 | **−23 %** ❌ | refazer |
| Frontal dir. | 18 × 18 | 420 | PLA | 350 | **−23 %** ❌ | refazer |
| Traseira esq. | 22 × 22 | 420 | PETG | 350 | **+12 %** ✅ | ok |
| Traseira dir. | 22 × 22 | 420 | PETG | 350 | **+12 %** ✅ | ok |

**Proposta:** aumentar a seção das frontais para 22 mm e trocar para PETG.

| Opção | Custo | Prazo |
|:---|---:|:---:|
| A — só seção | baixo | 1 dia |
| B — seção + PETG | médio | 2 dias |

---

1. Refazer as pernas frontais
2. Testar com `measure` e `joint_test`
   - conferir a folga de 0,2 mm

```python
bpy.ops.mesh.primitive_cube_add(size=22)
    # indentação preservada
```
