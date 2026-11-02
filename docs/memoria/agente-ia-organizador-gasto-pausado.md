---
name: agente-ia-organizador-gasto-pausado
description: El proyecto GCP agente-ia-organizador tiene el gasto pausado desde 2026-08-31; el backend no responde a propósito
metadata:
  type: project
---

El proyecto GCP `agente-ia-organizador` (número `1074615938998`, cuenta de facturación `014AF5-FDBEE6-B20CF6`) tiene el gasto pausado desde el 2026-08-31. La facturación sigue **habilitada** a propósito, para no arriesgar los datos de Firestore.

Qué se hizo para apagar el gasto:
- Cloud Scheduler `tick` (`0 * * * *`, us-central1) → **PAUSED**. Era el que despertaba el backend cada hora.
- Cloud Run `assistant-backend` (us-central1) → se le quitó `allUsers` de `roles/run.invoker`. Sigue desplegado y Ready, pero ya no es público. Único invoker: `scheduler-invoker@agente-ia-organizador.iam.gserviceaccount.com`.
- El servicio no tiene `minScale` ni CPU siempre asignada, así que escala a cero y cuesta $0 en reposo. Por eso no hizo falta borrarlo.

Si `https://assistant-backend-oldaiddxpq-uc.a.run.app` devuelve 403, es esto, no un bug. Para revertir: `gcloud scheduler jobs resume tick` y volver a agregar `allUsers` como invoker.

Ver [[preferir-pausar-gasto-antes-que-desvincular-facturacion]] y [[no-deploy-without-asking]].
