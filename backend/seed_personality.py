"""
Carga el perfil de personalidad del usuario en Firestore (assistant_users/<user_id>).

Por ahora: Eneagrama Type 3 (The Achiever) extraído del PDF test_result_type_3.pdf.
Cuando llegue Big 5 / MBTI se agrega al mismo doc bajo personality.{big5,mbti,...}.

Uso:
    python seed_personality.py
"""

from __future__ import annotations

import os
import sys

try:
    from google.cloud import firestore
except ImportError:
    print("ERROR: pip install google-cloud-firestore")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


PROJECT_ID = os.getenv("GCP_PROJECT_ID", "agente-ia-organizador")
PREFIX = os.getenv("FIRESTORE_COLLECTION_PREFIX", "assistant")
USER_ID = os.getenv("USER_ID", "noe")


PERSONALITY = {
    "enneagram": {
        "type": 3,
        "name": "The Achiever",
        "subtype": None,  # social | self_preservation | one_to_one — completar si la usuaria lo sabe
        "core_drive": (
            "Búsqueda incansable de éxito, ambición y excelencia. Distinguirse y "
            "lograr grandeza en lo elegido."
        ),
        "core_motivations": [
            "Reconocimiento, validación, logro",
            "Superar expectativas y excelir",
            "Mantener imagen de éxito y competencia",
        ],
        "core_fears": [
            "Fracaso y rechazo",
            "Ser expuesta como fraude o impostor",
            "No estar a la altura de propias o ajenas expectativas",
            "Que la imagen quede empañada",
        ],
        "characteristics": [
            "ambicion_y_logro",
            "image_consciousness",
            "adaptabilidad",
            "competitividad",
            "work_ethic_fuerte",
            "strategic_thinking",
            "carisma_e_influencia",
            "emotional_intelligence",
        ],
        "stress_patterns": {
            "manifests_as": "presión, abrumamiento, ansiedad cuando percibe estar por debajo de expectativas",
            "core_trigger": "miedo a ser expuesta como fraude",
            "go_to_coping": ["overwork", "perfeccionismo", "esconder vulnerabilidad"],
            "healthy_coping": [
                "ejercicio",
                "meditación / mindfulness",
                "tiempo en naturaleza",
                "yoga / breathing",
                "red de apoyo (familia/amigos)",
            ],
        },
        "growth_path": {
            "core_work": "Balancear ambición con autenticidad",
            "practices": [
                "Self-reflection regular (journaling, meditación)",
                "Diferenciar validación externa vs motivación genuina",
                "Aceptar vulnerabilidad — dejar de proyectar imagen perfecta",
                "Conectar con valores propios, no con métricas externas",
                "Priorizar bienestar personal sobre logro",
            ],
            "key_question": "¿Esto lo quiero yo o lo quiero porque me validará?",
        },
        "relationships": {
            "strengths": ["encanto", "carisma", "atención", "passion", "rapport"],
            "challenges": [
                "Priorizar trabajo sobre conexión personal",
                "Dificultad para estar plenamente presente",
                "Esconder vulnerabilidad detrás de fachada",
            ],
            "tips": [
                "Honestidad/transparencia con pareja sobre miedos",
                "Tiempo de calidad sin productividad de fondo",
                "Cultivar intimidad emocional, no solo logística",
            ],
        },
        "work": {
            "thrives_in": [
                "Ambientes dinámicos y competitivos",
                "Roles con visibilidad y reconocimiento",
                "Liderazgo, estrategia, ventas, marketing, entrepreneurship",
            ],
            "common_challenges": [
                "workaholism",
                "perfeccionismo",
                "burnout",
                "buscar validación externa como combustible principal",
            ],
            "career_advice": [
                "Alinear metas profesionales con valores personales",
                "Setear límites (work-life balance real)",
                "Self-compassion ante errores",
            ],
        },
        "common_traps": [
            "Sacrificar bienestar por mantener imagen",
            "Negligir aspiraciones profundas por expectativas externas",
            "Sentir vacío después de alcanzar metas",
            "Workaholism como evitación de vulnerabilidad",
        ],
    },
    # Placeholders para cuando lleguen otros tests
    "big5": None,
    "mbti": None,
    "other_tests": [],
}


# Traits que el principle selector puede usar (matching contra personality_relevance de cada principio)
CURRENT_STRUGGLES = [
    "perfectionism",
    "workaholism",
    "low_conscientiousness",   # mencionado por la usuaria directamente
    "all_or_nothing",
    "avoidant_vulnerability",
    "external_validation_dependency",
]


# Config de mensajes proactivos
PROACTIVE_CONFIG = {
    "daily_morning": True,     # 8am Lima — "¿qué priorizas hoy?"
    "daily_evening": True,     # 9pm Lima — "¿cómo te fue?"
    "silent_period_alerts": True,
    "pattern_alerts": True,
    "max_per_day": 3,
}


def main():
    print(f"Connecting to Firestore project: {PROJECT_ID}")
    db = firestore.Client(project=PROJECT_ID)
    doc_ref = db.collection(f"{PREFIX}_users").document(USER_ID)

    # Merge para no pisar otros campos del perfil existente
    payload = {
        "personality": PERSONALITY,
        "current_struggles": CURRENT_STRUGGLES,
        "proactive_messages": PROACTIVE_CONFIG,
    }

    doc_ref.set(payload, merge=True)
    print(f"\n✓ Personality saved into {PREFIX}_users/{USER_ID}")
    print(f"  - Enneagram type: {PERSONALITY['enneagram']['type']} ({PERSONALITY['enneagram']['name']})")
    print(f"  - Current struggles: {CURRENT_STRUGGLES}")
    print(f"  - Proactive config: {PROACTIVE_CONFIG}")


if __name__ == "__main__":
    main()
