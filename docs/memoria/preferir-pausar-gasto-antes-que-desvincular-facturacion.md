---
name: preferir-pausar-gasto-antes-que-desvincular-facturacion
description: Cuando pida deshabilitar facturación en GCP, ofrecer primero pausar el gasto sin desvincular
metadata:
  type: feedback
---

Cuando pide "deshabilitar la facturación" de un proyecto GCP, primero hay que revisar qué hay corriendo y ofrecerle la alternativa de apagar solo el gasto (pausar schedulers, cerrar acceso público, borrar servicios) dejando la facturación vinculada. En 2026-08-31, con `agente-ia-organizador`, eligió esa opción en vez de desvincular.

**Why:** desvincular la facturación deja Firestore inaccesible y Google puede borrar los datos si pasa ~30 días así. El objetivo real suele ser dejar de gastar, no arriesgar datos.

**How to apply:** antes de `gcloud billing projects unlink`, inventariar el proyecto (Cloud Run, Scheduler, Firestore, buckets), mostrarle el impacto por recurso y dejarla decidir. Ojo: Cloud Run sin `minScale` ya cuesta $0 en reposo, así que casi nunca hay que borrarlo — basta cortar lo que lo despierta.

Ver [[agente-ia-organizador-gasto-pausado]].
