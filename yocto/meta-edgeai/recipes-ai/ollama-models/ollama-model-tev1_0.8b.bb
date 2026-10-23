SUMMARY = "Modelo de decision tev1:0.8b horneado en la imagen"
DESCRIPTION = "Modelo System One servido por /v1/systemone. Carril de decision \
del asistente: clasificacion tipada con probabilidad, sin generacion de texto."

OLLAMA_MODEL_TAR = "tev1-0.8b-ollama.tar"
OLLAMA_MODEL_SHA = "13238a37a0496c4ac0ec3b8b016ef8536107004bd3933abf4c4924e4152260eb"

SRC_URI = "file://${OLLAMA_MODEL_TAR}"

inherit ollama-model
