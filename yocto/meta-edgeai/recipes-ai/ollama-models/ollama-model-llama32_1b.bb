SUMMARY = "Modelo generativo llama3.2:1b horneado en la imagen"
DESCRIPTION = "LLM pequeno servido por /api/chat. Carril generativo del asistente: \
resumenes del dia, preguntas abiertas y reescritura de texto. Componente opcional: \
el sistema opera sin el, con el catalogo reducido a operaciones de decision."

OLLAMA_MODEL_TAR = "llama3.2-1b-ollama.tar"
OLLAMA_MODEL_SHA = "1e173a9e6996d33b379b3d7a3350e3bddd138d108d0ecbac54a37fe89e1b2c5d"

SRC_URI = "file://${OLLAMA_MODEL_TAR}"

inherit ollama-model
