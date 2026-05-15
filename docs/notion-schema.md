# Notion Database Schema

Estructura de las databases de Notion que usa el asistente.

## Database: Tasks

| Property | Type | Values / Notes |
|---|---|---|
| Name | title | Nombre de la tarea |
| Status | select | `Inbox`, `Next`, `In Progress`, `Waiting`, `Done` |
| Priority | select | `P0`, `P1`, `P2`, `P3` |
| Due Date | date | Fecha límite |
| Scheduled Date | date | Fecha programada para ejecutar |
| Time Estimate | number | Minutos estimados |
| Energy Level | select | `High`, `Medium`, `Low` |
| Project | relation | → Projects database |
| Goal | relation | → Goals database |
| Tags | multi_select | Etiquetas libres |
| Created By | select | `Voice`, `Text`, `Manual` |

## Database: Notes / Inbox

| Property | Type | Values / Notes |
|---|---|---|
| Name | title | Título de la nota |
| Source | select | `Voice`, `Text`, `Meeting`, `Idea`, `Quick Capture` |
| Status | select | `Unprocessed`, `Processed` |
| Tags | multi_select | Etiquetas libres |
| Related Task | relation | → Tasks database |
| Audio URL | url | Link al audio original en Cloud Storage |
| Created | created_time | Automático |

*El contenido principal va en el body de la página como bloques de párrafo.*

## Database: Goals

| Property | Type | Values / Notes |
|---|---|---|
| Name | title | Nombre de la meta |
| Type | select | `Short Term` (1-3 meses), `Medium Term` (3-12 meses), `Long Term` (1+ años) |
| Status | select | `Active`, `Completed`, `Paused`, `Cancelled` |
| Progress | number | Porcentaje (0-100) |
| Target Date | date | Fecha objetivo |
| Key Results | rich_text | Resultados clave |
| Area | select | `Work`, `Personal`, `Health`, `Finance`, `Learning`, `Relationships` |
| Projects | relation | → Projects database |

## Database: Daily Agenda

| Property | Type | Values / Notes |
|---|---|---|
| Name | title | Título (ej: "Plan 2024-01-15") |
| Date | date | Fecha del día |
| Top 3 | relation | → Tasks (3 prioridades del día) |
| Score | number | Autoevaluación 1-10 |
| Wins | rich_text | Logros del día |

*El plan con time blocks y la reflexión van en el body de la página.*

---

## Setup en Notion

1. Crear una **Internal Integration** en https://www.notion.so/my-integrations
2. Copiar el **Internal Integration Token** → `NOTION_TOKEN`
3. Crear las 4 databases con las propiedades listadas arriba
4. **Conectar** la integration a cada database (Share → Invite → seleccionar tu integration)
5. Copiar los **Database IDs** de la URL de cada database → `.env`

### Cómo obtener el Database ID

La URL de una database tiene este formato:
```
https://www.notion.so/workspace/DATABASE_ID?v=VIEW_ID
```

El `DATABASE_ID` es la cadena de 32 caracteres antes del `?v=`.
