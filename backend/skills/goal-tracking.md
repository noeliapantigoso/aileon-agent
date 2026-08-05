---
name: goal-tracking
description: Track progress on long/medium/short-term goals
triggers: [goal, meta, objetivo, progreso, progress, logro, achievement, milestone, hito, avance, propósito]
---

# Goal Tracking

Use this skill when the user mentions goals, wants to check progress, update milestones, or connect today's work to their bigger objectives.

## Goal types

- `long_term`: 1+ years (career direction, life goals)
- `medium_term`: 3–12 months (projects, skills, habits to build)
- `short_term`: 1–3 months (specific deliverables, immediate targets)

## Creating a goal — flow obligatorio

**NUNCA crear una meta sin fecha límite ni sin KRs.** Seguir siempre estos pasos:

### Paso 1 — recopilar datos (en una sola pregunta si faltan)
Antes de llamar cualquier tool, asegurarse de tener:
- Título claro de la meta
- Fecha límite (`target_date`) — si el usuario dice "en 6 meses" calcularla como fecha exacta
- Área (`area`): work / personal / health / finance / learning / relationships
- Al menos 2 KRs concretos y observables

Si falta algo, preguntar todo junto de una vez:
> "Para crear la meta necesito: ¿cuál es la fecha límite? ¿A qué área pertenece (trabajo, aprendizaje, salud...)? ¿Cuáles serían los 2-4 resultados concretos que indicarían que la lograste?"

### Paso 2 — crear la meta
Llamar `create_goal` con title, goal_type, area y target_date.

### Paso 3 — crear los KRs
Para cada KR, llamar `create_key_result(goal_id, title)`.
Los KRs deben ser observables: "Completar el curso X", "Publicar 3 artículos", "Alcanzar 10k seguidores".
No crear KRs vagos como "mejorar en ML" — reformularlos antes.

### Paso 4 — mostrar resumen
Responder con un resumen claro:
```
✅ Meta creada: [título]
📅 Fecha límite: [fecha]
🎯 Key Results:
  1. [KR1]
  2. [KR2]
  3. [KR3]
```

## Consultar y actualizar metas

Use `get_goals` para ver el estado actual.
Use `get_key_results(goal_id)` para ver KRs de una meta específica.
Use `complete_key_result(kr_id)` cuando un KR se alcanza — recalcula el progreso automáticamente.
Use `update_goal_progress` solo como fallback para metas sin KRs.
Use `update_goal` para cambiar título, fecha, área o status (active → paused → completed).

**Cuándo marcar un KR como done:**
- Usuario dice explícitamente "completé X" o "terminé X"
- El contexto lo hace inequívoco ("publiqué mi quinto artículo" → KR "publicar 5 artículos" done)
- En el daily review, si un bloque completado claramente satisface un KR

## Connecting tasks to goals

When creating a task linked to a goal, pass `goal_id` to `create_task`.

## Tone around goals

- Goals are personal — treat them with weight, not as checkboxes
- If a goal hasn't been touched in a while, ask once (don't repeat every day)
- Celebrate when a KR or goal is reached — it deserves a real acknowledgment

## Tone around goals

- Goals are personal — treat them with weight, not as checkboxes
- If a goal hasn't been touched in a while, ask once (don't repeat every day)
- If the user seems stuck on a goal, ask what's in the way — don't just reschedule the deadline
- Celebrate when a goal is reached — it deserves a real acknowledgment, not just "✅ Done"

## What to avoid

- Don't create goals from single mentions — confirm before using `create_goal`
- Don't pressure about goals; ask, then respect the answer
