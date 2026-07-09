# CLAUDE.md

Guía para sesiones de Claude Code en este repo. Leela antes de tocar nada.

## Convenciones de infraestructura

- La infra compartida (`Dockerfile`, `.dockerignore`, `requirements.txt`) se toca **solo en un PR propio, chico y dedicado**, nunca dentro de un PR de feature o demo.
- **Rebaseá sobre `main` antes de mergear.** El ruleset `protect-main` exige historia lineal, así que el merge es por rebase o squash; rebasear primero hace que un cambio a infra se edite contra el estado actual y cualquier conflicto salga a la luz en vez de sobrescribirse en silencio.
- Motivo: dos ramas que tocan el mismo archivo raíz compartido desde bases distintas hacen que la última en mergear reescriba esa región y pueda pisar cambios previos (ej. el `GIT_COMMIT` del Dockerfile).
