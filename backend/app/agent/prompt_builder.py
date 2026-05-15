"""
Constructor dinámico del system prompt para el agente.

El prompt se ensambla en runtime para cada request, inyectando:
- Perfil del usuario
- Contexto temporal (fecha, hora, día)
- Contexto de trabajo (agenda, tareas de hoy)
- Memorias relevantes de Mem0
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

# Mapeo de días de la semana al español
DAYS_ES = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
    6: "domingo",
}


def build_system_prompt(
    user_profile: dict[str, Any],
    today_context: dict[str, Any],
    relevant_memories: list[str],
    current_datetime: datetime,
    relevant_principles: list[dict[str, Any]] | None = None,
    active_insights: list[dict[str, Any]] | None = None,
) -> str:
    """
    Ensambla el system prompt completo con todo el contexto.

    Args:
        user_profile: Perfil del usuario desde Firestore.
        today_context: Agenda de hoy, tareas pendientes, etc.
        relevant_memories: Memorias episódicas relevantes de Mem0.
        current_datetime: Fecha y hora actual.

    Returns:
        System prompt completo como string.
    """
    name = user_profile.get("name", "usuario")
    occupation = user_profile.get("occupation", "")
    productivity = user_profile.get("productivity", {})
    preferences = user_profile.get("preferences", {})
    comm_style = preferences.get("communication_style", "Directo y conciso.")

    day_name = DAYS_ES.get(current_datetime.weekday(), "")
    date_str = current_datetime.strftime("%d de %B de %Y")
    time_str = current_datetime.strftime("%H:%M")

    sections = []

    # ── 1. ROL Y PERSONALIDAD ────────────────────────────────────────────
    sections.append(f"""## Rol y personalidad

Eres el asistente personal de productividad de {name}. Tu trabajo es ayudarle a \
organizar su día, gestionar tareas, capturar ideas y mantener el foco en sus metas.

Reglas de comunicación:
- Responde SIEMPRE en español
- Tutea al usuario
- Tono: directo, eficiente, empático, con humor sutil cuando sea apropiado
- Estilo: {comm_style}
- Si es una acción simple (guardar nota, crear tarea), confirma brevemente: "✅ Guardado" o similar
- Si es una pregunta, responde concisamente
- Si es planificación, propón un plan estructurado
- No repitas lo que el usuario dijo, actúa directamente""")

    # ── 2. CONTEXTO TEMPORAL ─────────────────────────────────────────────
    sections.append(f"""## Contexto temporal

- Fecha: {day_name}, {date_str}
- Hora actual: {time_str}
- Zona horaria: America/Lima (UTC-5)""")

    # ── 3. PERFIL DEL USUARIO ────────────────────────────────────────────
    profile_lines = [f"## Perfil del usuario\n\n- Nombre: {name}"]
    if occupation:
        profile_lines.append(f"- Ocupación: {occupation}")

    company = user_profile.get("company", "")
    if company:
        profile_lines.append(f"- Empresa: {company}")

    if productivity:
        peak = productivity.get("peak_hours", "")
        if peak:
            profile_lines.append(f"- Horas de mayor productividad: {peak}")
        secondary = productivity.get("secondary_peak", "")
        if secondary:
            profile_lines.append(f"- Pico secundario: {secondary}")
        low = productivity.get("low_energy_hours", "")
        if low:
            profile_lines.append(f"- Horas de baja energía: {low}")
        work_start = productivity.get("work_start", "")
        work_end = productivity.get("work_end", "")
        if work_start and work_end:
            profile_lines.append(f"- Horario de trabajo: {work_start} - {work_end}")
        focus_block = productivity.get("preferred_focus_block_minutes")
        if focus_block:
            profile_lines.append(f"- Bloque de enfoque preferido: {focus_block} minutos")

    # Metas del perfil
    goals = user_profile.get("goals", {})
    if goals:
        for goal_type, goal_list in goals.items():
            if goal_list:
                label = goal_type.replace("_", " ").title()
                profile_lines.append(f"- Metas ({label}):")
                for g in goal_list:
                    if isinstance(g, dict):
                        profile_lines.append(f"  - {g.get('name', g)}")
                    else:
                        profile_lines.append(f"  - {g}")

    # Struggles actuales
    struggles = user_profile.get("current_struggles", [])
    if struggles:
        profile_lines.append(f"- Áreas de trabajo personal: {', '.join(struggles)}")

    sections.append("\n".join(profile_lines))

    # ── 3b. PERSONALIDAD ─────────────────────────────────────────────────
    personality = user_profile.get("personality", {})
    enneagram = personality.get("enneagram") if isinstance(personality, dict) else None
    if enneagram and isinstance(enneagram, dict):
        pers_lines = ["## Perfil de personalidad (Eneagrama)"]
        pers_lines.append(
            f"**Tipo {enneagram.get('type')}: {enneagram.get('name', '')}**"
        )
        if enneagram.get("subtype"):
            pers_lines.append(f"Subtipo: {enneagram['subtype']}")

        if enneagram.get("core_drive"):
            pers_lines.append(f"\n**Motor central:** {enneagram['core_drive']}")

        motivations = enneagram.get("core_motivations", [])
        if motivations:
            pers_lines.append("\n**Motivaciones:**")
            for m in motivations:
                pers_lines.append(f"- {m}")

        fears = enneagram.get("core_fears", [])
        if fears:
            pers_lines.append("\n**Miedos:**")
            for f in fears:
                pers_lines.append(f"- {f}")

        stress = enneagram.get("stress_patterns", {})
        if stress:
            pers_lines.append(
                f"\n**Estrés se manifiesta como:** {stress.get('manifests_as', '')}"
            )
            trigger = stress.get("core_trigger")
            if trigger:
                pers_lines.append(f"**Trigger central:** {trigger}")

        growth = enneagram.get("growth_path", {})
        if growth:
            pers_lines.append(f"\n**Trabajo de crecimiento:** {growth.get('core_work', '')}")
            if growth.get("key_question"):
                pers_lines.append(f"**Pregunta clave para ofrecer:** *{growth['key_question']}*")

        traps = enneagram.get("common_traps", [])
        if traps:
            pers_lines.append("\n**Trampas comunes a vigilar:**")
            for t in traps:
                pers_lines.append(f"- {t}")

        pers_lines.append(
            "\n*Usa este perfil para personalizar tus respuestas: respeta sus miedos, "
            "no refuerces validación externa vacía, ayúdala a balancear ambición con "
            "autenticidad y bienestar. Ofrece la pregunta clave cuando sospeches que "
            "está actuando por validación externa.*"
        )

        sections.append("\n".join(pers_lines))

    # ── 4. AGENDA DE HOY ─────────────────────────────────────────────────
    agenda_lines = ["## Contexto del día"]

    today_tasks = today_context.get("today_tasks", [])
    if today_tasks:
        agenda_lines.append("\n### Tareas programadas para hoy:")
        for task in today_tasks[:10]:
            status = task.get("status", "?")
            priority = task.get("priority", "")
            title = task.get("title", "Sin título")
            estimate = task.get("time_estimate", "")
            line = f"- [{status}] {title}"
            if priority:
                line += f" ({priority})"
            if estimate:
                line += f" ~{estimate}min"
            agenda_lines.append(line)
    else:
        agenda_lines.append("\nNo hay tareas programadas específicamente para hoy.")

    pending = today_context.get("pending_tasks", [])
    if pending:
        agenda_lines.append("\n### Tareas pendientes (sin fecha):")
        for task in pending[:8]:
            priority = task.get("priority", "")
            title = task.get("title", "Sin título")
            line = f"- {title}"
            if priority:
                line += f" ({priority})"
            agenda_lines.append(line)

    agenda = today_context.get("agenda", {})
    if agenda.get("exists"):
        agenda_lines.append("\n### Plan del día: Ya existe un plan generado.")

    active_goals = today_context.get("active_goals", [])
    if active_goals:
        agenda_lines.append("\n### Metas activas (todos los tipos):")
        by_type: dict[str, list[dict[str, Any]]] = {"long_term": [], "medium_term": [], "short_term": [], "other": []}
        type_map_inv = {
            "Long Term": "long_term",
            "Medium Term": "medium_term",
            "Short Term": "short_term",
        }
        for g in active_goals:
            t = type_map_inv.get(g.get("type", ""), "other")
            by_type[t].append(g)

        labels = {
            "long_term": "Largo plazo (1+ años)",
            "medium_term": "Mediano plazo (3-12 meses)",
            "short_term": "Corto plazo (1-3 meses)",
            "other": "Otras",
        }
        for key in ["long_term", "medium_term", "short_term", "other"]:
            goals_in_type = by_type[key]
            if not goals_in_type:
                continue
            agenda_lines.append(f"\n**{labels[key]}:**")
            for g in goals_in_type[:6]:
                title = g.get("title", "Sin título")
                progress = g.get("progress")
                area = g.get("area", "")
                target = g.get("target_date", "")
                progress_pct = (
                    f"{int(progress * 100)}%" if isinstance(progress, float) and progress <= 1
                    else (f"{progress}%" if progress else "0%")
                )
                line = f"- {title} [{progress_pct}]"
                if area:
                    line += f" ({area})"
                if target:
                    line += f" → {target}"
                agenda_lines.append(line)

    sections.append("\n".join(agenda_lines))

    # ── 5. MEMORIAS RELEVANTES ───────────────────────────────────────────
    if relevant_memories:
        memory_lines = ["## Memorias relevantes\n"]
        memory_lines.append(
            "Información de conversaciones anteriores que puede ser útil:\n"
        )
        for i, memory in enumerate(relevant_memories, 1):
            memory_lines.append(f"{i}. {memory}")
        sections.append("\n".join(memory_lines))

    # ── 5a. INSIGHTS DETECTADOS ──────────────────────────────────────────
    if active_insights:
        ins_lines = ["## Patrones detectados (de análisis previos)\n"]
        ins_lines.append(
            "Estos patrones fueron detectados analizando interacciones pasadas. "
            "TENELOS EN CUENTA al responder — son datos del usuario, no inventes. "
            "Mencionalos solo si son directamente relevantes al mensaje actual.\n"
        )
        for ins in active_insights:
            ins_lines.append(
                f"- **{ins.get('title', '')}** ({ins.get('category', '')}): "
                f"{ins.get('description', '')} → {ins.get('actionable', '')}"
            )
        sections.append("\n".join(ins_lines))

    # ── 5b. KNOWLEDGE BASE — PRINCIPIOS ──────────────────────────────────
    if relevant_principles:
        princ_lines = ["## Knowledge base — Principios disponibles\n"]
        princ_lines.append(
            "Tienes acceso a estos principios destilados de Jordan Peterson. "
            "EVALUÁ EL MENSAJE Y EL CONTEXTO, después ELEGÍ 1-3 principios que "
            "REALMENTE apliquen a esta situación específica y usalos para guiar "
            "tu respuesta. Si NINGUNO aplica, no fuerces — respondé sin invocar "
            "principios. No los cites textualmente a menos que la quote aporte "
            "fuerza emocional al momento.\n"
        )
        for i, p in enumerate(relevant_principles, 1):
            applies = ", ".join(p.get("applies_when", []))
            princ_lines.append(
                f"### {i}. {p.get('title', '')} (id: `{p.get('id', '')}`)\n"
                f"**Principio:** {p.get('principle', '')}\n"
                f"**Aplica cuando:** {applies}\n"
                f"**Acción concreta:** {p.get('actionable', '')}\n"
                f"**Quote:** \"{p.get('quote', '')}\""
            )
        sections.append("\n\n".join(princ_lines))

    # ── 6. INSTRUCCIONES DE COMPORTAMIENTO ───────────────────────────────
    sections.append("""## Instrucciones de comportamiento

- Para inputs de voz: puede haber errores de transcripción, interpreta con contexto
- No siempre hace falta responder largo. "✅ Guardado" o "Listo, tarea creada" es válido para acciones simples
- Si el usuario menciona algo personal (logro, frustración), reconócelo brevemente antes de actuar
- Si detectas conflictos de horario o sobrecarga de tareas, advierte proactivamente
- Al priorizar tareas, usa la matriz Eisenhower (urgente/importante)
- Sugiere bloques de enfoque durante las horas de mayor productividad del usuario
- Cuando organices un día, agrupa tareas similares y respeta los ciclos de energía
- Si no entiendes algo o falta contexto, pregunta antes de asumir
- Las fechas siempre en formato YYYY-MM-DD para las tools""")

    return "\n\n".join(sections)
