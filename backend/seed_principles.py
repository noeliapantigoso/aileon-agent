"""
Carga los principios destilados de los videos de Jordan Peterson
en la colección Firestore `assistant_principles`.

Uso:
    python seed_principles.py
"""

from __future__ import annotations

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

import os

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "agente-ia-organizador")
PREFIX = os.getenv("FIRESTORE_COLLECTION_PREFIX", "assistant")


PRINCIPLES = [
    {
        "id": "scaffold_alternatives",
        "title": "Levanta scaffolds alternativos",
        "principle": "Si vas a apostar fuerte a algo, ten 2-3 caminos paralelos viables. Si una falla, el piso no cae.",
        "applies_when": ["decision_grande", "miedo_apostar", "elegir_carrera", "cambio_trabajo", "riesgo"],
        "personality_relevance": ["high_neuroticism", "low_risk_tolerance"],
        "quote": "If you're gonna stake yourself on something, throw a couple alternative scaffolds up beside you so that you have somewhere to go.",
        "actionable": "Lista 2 alternativas que cubran 80% de lo que quieres. Validalas antes de apostar.",
        "source": "JP_video_1",
    },
    {
        "id": "wholeness_vs_perfection",
        "title": "Wholeness vs Perfection",
        "principle": "Cinco áreas al 80% > una al 150%. Vida con riqueza vs vida con un solo punto de falla.",
        "applies_when": ["agotamiento", "obsesion_carrera", "vida_desbalanceada", "elegir_prioridades"],
        "personality_relevance": ["high_openness", "burnout_risk"],
        "quote": "A lot more richness in a life where you have five things operating at 80%.",
        "actionable": "Lista tus 5 áreas (carrera, relaciones, salud, hobbies, civic). Ranquéa 0-100. La que está <40 es donde poner foco.",
        "source": "JP_video_1",
    },
    {
        "id": "adventure_over_comfort",
        "title": "Aventura > Comodidad",
        "principle": "Antídoto al malestar no es comodidad sino aventura hacia la excelencia. Comodidad es sedación.",
        "applies_when": ["estancamiento", "zona_confort", "evitar", "procrastinar", "ansiedad"],
        "personality_relevance": ["high_neuroticism", "low_conscientiousness"],
        "quote": "You might lose your body out there in the world but if you stay here you lose your soul.",
        "actionable": "Identifica la cosa mínima que sería 'aventura' hoy. Hacela aunque incomode.",
        "source": "JP_video_3",
    },
    {
        "id": "micro_goals",
        "title": "Micro-goals calibrados a tu nivel",
        "principle": "Goal grande → fragmenta hasta encontrar un paso tan pequeño que sí lo hagas. La humildad de empezar tan abajo como sea necesario.",
        "applies_when": ["no_empezar", "procrastinar", "abrumamiento", "meta_grande", "disciplina"],
        "personality_relevance": ["low_conscientiousness", "perfectionism"],
        "quote": "Take your goal and fragment it into micro-goals until you find one small enough that you will do it.",
        "actionable": "Toma tu meta. Bájala 5 niveles hasta que sea ridículamente chica. Ese es tu paso de hoy.",
        "source": "JP_video_2",
    },
    {
        "id": "honor_contract_with_self",
        "title": "Honra el contrato contigo misma",
        "principle": "Si acordaste hacer X mínimo, NO hagas más cuando te emociones. Romper el contrato hacia arriba destruye la confianza igual que romperlo hacia abajo.",
        "applies_when": ["habito_nuevo", "sobreesfuerzo", "burnout_ciclo", "todo_o_nada"],
        "personality_relevance": ["perfectionism", "high_neuroticism", "all_or_nothing"],
        "quote": "Don't push your bloody luck. Abide by the contract you've written with yourself.",
        "actionable": "Si acordaste 5min, hacé 5min y parás. Aunque sientas que podés más.",
        "source": "JP_video_2",
    },
    {
        "id": "room_as_microcosm",
        "title": "Tu cuarto es microcosmos de tu vida",
        "principle": "El estado de tu entorno inmediato refleja el estado interno. Ordenar el caos físico = empezar a ordenar el caos vital.",
        "applies_when": ["caos_general", "no_sé_por_donde_empezar", "estancamiento", "depresion_funcional"],
        "personality_relevance": ["low_conscientiousness", "depression_signs"],
        "quote": "That chaos in his immediate environment was deeply emblematic of the chaos of his life.",
        "actionable": "Elige UNA superficie de tu cuarto. Ordenala. Solo esa. Hoy.",
        "source": "JP_video_2",
    },
    {
        "id": "progress_is_nonlinear",
        "title": "Progreso no es lineal",
        "principle": "El primer paso es el más caro. Una vez que empieza, acelera no-linealmente.",
        "applies_when": ["primer_paso", "desmotivacion", "comparar_progreso", "expectativas_irrealistas"],
        "personality_relevance": ["high_neuroticism", "impatience"],
        "quote": "Once you take the first step, the probability that you'll take a slightly larger second step increases.",
        "actionable": "No midas progreso lineal. Mide si diste UN paso esta semana. Sí = vas bien.",
        "source": "JP_video_2",
    },
    {
        "id": "communicate_explicitly",
        "title": "Comunica exactamente qué necesitas",
        "principle": "Si querés algo del otro, decile las palabras exactas que te darían eso. No esperes que adivine. 'Si me amaras lo sabrías' es falso.",
        "applies_when": ["conflicto_pareja", "frustracion_relacional", "esperar_que_adivinen"],
        "personality_relevance": ["high_agreeableness", "avoidant_communication"],
        "quote": "Just because I love you doesn't mean I'm not stupid.",
        "actionable": "Identificá una fricción. Escribí las palabras exactas que te darían lo que necesitás. Decilas.",
        "source": "JP_video_2",
    },
    {
        "id": "reward_small_increments",
        "title": "Recompensa pasos chiquitos",
        "principle": "En relaciones (con otros y contigo), recompensar el intento torpe es la única forma de que mejore. Castigar lo imperfecto mata el siguiente intento.",
        "applies_when": ["enseñar_pareja", "cambio_habito", "auto_critica"],
        "personality_relevance": ["high_neuroticism", "perfectionism"],
        "quote": "Help your partner take stupid incremental steps forward.",
        "actionable": "Cuando alguien (o vos misma) intente algo nuevo torpemente: notalo, agradece, no critiques.",
        "source": "JP_video_2",
    },
    {
        "id": "hierarchy_competence",
        "title": "Jerarquías funcionan por competencia, no poder",
        "principle": "En sistemas sanos, la posición la determina la competencia y ética, no el poder. Tu trabajo: ser competente y ético, no manipular.",
        "applies_when": ["trabajo_injusto", "promocion", "comparar_carrera", "victimizacion"],
        "personality_relevance": ["high_neuroticism", "external_locus"],
        "quote": "It's ethics that determines success in a functional society, not power.",
        "actionable": "Pregúntate: ¿qué competencia me falta para subir? Trabajá en esa. Si el sistema es corrupto, salí.",
        "source": "JP_video_4",
    },
    {
        "id": "six_week_experiment",
        "title": "Experimento de 6 semanas",
        "principle": "Antes de renunciar/cambiar, probá 6 semanas trabajando 15min antes y 15min después con foco total. Sin cinismo. Mide resultados.",
        "applies_when": ["renunciar", "frustracion_trabajo", "estancamiento_carrera"],
        "personality_relevance": ["impatience", "low_conscientiousness"],
        "quote": "He started at $21/hour and in six weeks he was making $37/hour.",
        "actionable": "Decidí fecha de inicio. 6 semanas. 15min extra al inicio y al final. Diario nota del esfuerzo y resultados.",
        "source": "JP_video_4",
    },
    {
        "id": "dont_quit_without_plan",
        "title": "No renuncies sin plan B en marcha",
        "principle": "Si no tenés algo mejor montado, no sueltes lo que tenés aunque lo odies. Hold the edge while you build.",
        "applies_when": ["impulso_renunciar", "frustracion_aguda", "decision_emocional"],
        "personality_relevance": ["high_neuroticism", "impulsive"],
        "quote": "Don't quit your job. That's what you're hanging on to the edge of the world with your fingertips.",
        "actionable": "Antes de renunciar: CV listo, 25 aplicaciones enviadas, 1 oferta concreta. Recién ahí soltá.",
        "source": "JP_video_4",
    },
    {
        "id": "serotonin_position",
        "title": "Tu cuerpo trackea tu posición",
        "principle": "Serotonina sube/baja con tu posición jerárquica. Sentirte 'abajo' no es solo psicológico, es químico. Por eso progreso visible importa biológicamente.",
        "applies_when": ["depresion_funcional", "sentirse_inutil", "comparacion_social", "anhedonia"],
        "personality_relevance": ["high_neuroticism", "depression_signs"],
        "quote": "Your serotonin levels plummet like a defeated lobster.",
        "actionable": "Identificá UN avance visible esta semana. Aunque sea micro. Tu química lo necesita.",
        "source": "JP_video_4",
    },
    {
        "id": "speak_truth_civic",
        "title": "Hablar verdad como deber",
        "principle": "Cuando notás corrupción/disfunción en tu entorno (trabajo, relaciones, familia), nombrarlo es responsabilidad ética. El silencio acumula el caos.",
        "applies_when": ["incomodidad_entorno", "injusticia_observada", "miedo_confrontacion"],
        "personality_relevance": ["high_agreeableness", "conflict_avoidant"],
        "quote": "Keep your damn eyes open for the corruption and your mouth speaking truth.",
        "actionable": "Identificá UNA cosa que ves mal y callás. Decila esta semana — con tacto pero clara.",
        "source": "JP_video_4",
    },
    {
        "id": "choice_has_consequences",
        "title": "Cada decisión chica forma carácter",
        "principle": "Las pequeñas elecciones diarias (¿me levanto? ¿finjo enfermedad? ¿evito?) construyen quién serás. No 'son chicas', son acumulativas.",
        "applies_when": ["tentacion_evitar", "decision_diaria", "habito_evasion"],
        "personality_relevance": ["low_conscientiousness", "avoidant"],
        "quote": "The choice has consequences. The child's soon going to be an adult that's going to make very similar decisions.",
        "actionable": "Cuando estés por evitar algo hoy: pregúntate '¿qué versión de mí construye esta elección?'",
        "source": "JP_video_3",
    },
]


def main():
    print(f"Connecting to Firestore project: {PROJECT_ID}")
    db = firestore.Client(project=PROJECT_ID)
    collection = db.collection(f"{PREFIX}_principles")

    print(f"Seeding {len(PRINCIPLES)} principles into '{PREFIX}_principles'...")

    for p in PRINCIPLES:
        doc_ref = collection.document(p["id"])
        doc_ref.set(p)
        print(f"  ✓ {p['id']}")

    print(f"\nDone. {len(PRINCIPLES)} principles loaded.")


if __name__ == "__main__":
    main()
