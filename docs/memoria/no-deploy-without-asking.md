---
name: no-deploy-without-asking
description: Nunca hacer deploy (gcloud run deploy) sin que la usuaria lo pida explícitamente
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 90209c49-a2ce-4ec5-95ea-d318d5e13b23
  modified: 2026-08-06T18:19:08.416Z
---

No ejecutar `gcloud run deploy` ni ningún deploy a producción a menos que la usuaria lo pida explícitamente en ese momento. Hacer los cambios de código y esperar.

**Why:** La usuaria quiere revisar los cambios antes de que lleguen a producción. Un deploy automático después de editar código le quita ese control, y un deploy roto tumba el agente que usa a diario por Telegram.

**How to apply:** Terminar los edits, reportar qué cambió, y decir que queda listo para deploy cuando ella diga. Si pide deploy, usar siempre `--allow-unauthenticated` (ver [[deploy-allow-unauthenticated]]).
