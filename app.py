import streamlit as st
import PyPDF2
import re
import json
from datetime import datetime
import time

# Intentar importar Gemini
try:
    import google.generativeai as genai
    from google.api_core import exceptions
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    st.warning("Para usar análisis con IA, instala 'google-generativeai' (pip install google-generativeai)")

# ----------------------------------------------------------------------
# Funciones de utilidad
# ----------------------------------------------------------------------
def limpiar_texto(texto):
    """Elimina líneas vacías múltiples y espacios redundantes."""
    return re.sub(r'\n\s*\n', '\n', texto.strip())

def extraer_texto_pdf(archivo_pdf):
    """Extrae texto de un archivo PDF."""
    texto = ""
    try:
        lector = PyPDF2.PdfReader(archivo_pdf)
        num_paginas = len(lector.pages)
        for pagina in lector.pages:
            texto_pagina = pagina.extract_text()
            if texto_pagina:
                texto += texto_pagina + "\n"
        return texto, num_paginas
    except Exception as e:
        st.error(f"Error al leer el PDF: {e}")
        return None, 0

# ----------------------------------------------------------------------
# Funciones de extracción por regex (mantenidas del código original)
# ----------------------------------------------------------------------
# ... (aquí van todas las funciones de extracción: extraer_paciente, extraer_servicios, etc.)
# Por brevedad, no las repito, pero deben estar incluidas.

def extraer_paciente(texto):
    """Extrae datos básicos del paciente."""
    paciente = {}
    doc = re.search(r'CC\s*(\d+)', texto)
    if doc:
        paciente['documento'] = doc.group(1)
    nombre = re.search(r'--\s*([A-ZÁÉÍÓÚÑ\s]+?)\s+Fec\.\s*Nacimiento', texto)
    if nombre:
        paciente['nombre'] = nombre.group(1).strip()
    fn = re.search(r'Fec\.\s*Nacimiento:\s*(\d{2}/\d{2}/\d{4})', texto)
    if fn:
        paciente['fecha_nacimiento'] = fn.group(1)
    edad = re.search(r'Edad\s*actual:\s*(\d+)\s*AÑOS', texto)
    if edad:
        paciente['edad'] = int(edad.group(1))
    tel = re.search(r'Teléfono:\s*(\d+)', texto)
    if tel:
        paciente['telefono'] = tel.group(1)
    dire = re.search(r'Dirección:\s*([^\n]+)', texto)
    if dire:
        paciente['direccion'] = dire.group(1).strip()
    return paciente

def extraer_servicios(texto):
    servicios = []
    pattern = r'SEDE DE ATENCION\s+(\d+)\s+([^\n]+?)\s+FOLIO\s+\d+\s+FECHA\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})\s+TIPO DE ATENCION\s*:\s*([^\n]+)'
    for match in re.finditer(pattern, texto, re.IGNORECASE):
        servicios.append({
            'sede_codigo': match.group(1),
            'sede_nombre': match.group(2).strip(),
            'fecha': match.group(3),
            'hora': match.group(4),
            'tipo_atencion': match.group(5).strip()
        })
    return servicios

def extraer_diagnosticos(texto):
    diagnosticos = []
    patron = r'(?:DIAGN[OÓ]STICO|DX|DIAGN[OÓ]STICOS?)\s*:?\s*([A-Z0-9]+\s+[^\n]+)'
    for match in re.finditer(patron, texto, re.IGNORECASE):
        diag = match.group(1).strip()
        codigo = re.search(r'([A-Z]\d{2,3})', diag)
        diagnosticos.append({
            'codigo': codigo.group(1) if codigo else '',
            'descripcion': diag
        })
    return diagnosticos

def extraer_medicamentos(texto):
    # ... (mantener implementación original)
    return []  # Placeholder

def extraer_procedimientos(texto):
    # ... (mantener)
    return []

def extraer_cirugias(texto):
    # ... (mantener)
    return []

def extraer_laboratorios(texto):
    # ... (mantener)
    return []

def extraer_imagenes(texto):
    # ... (mantener)
    return []

def extraer_interconsultas(texto):
    # ... (mantener)
    return []

def extraer_evoluciones(texto):
    # ... (mantener)
    return []

def extraer_altas(texto):
    # ... (mantener)
    return []

# ----------------------------------------------------------------------
# Extracción mediante IA con Gemini
# ----------------------------------------------------------------------
def configure_gemini(api_key):
    genai.configure(api_key=api_key)

def extract_with_gemini(text, api_key, model_name="gemini-2.0-flash", max_chars=200000):
    """
    Envía el texto a Gemini y pide que devuelva un JSON estructurado.
    """
    configure_gemini(api_key)
    model = genai.GenerativeModel(model_name)
    
    # Limitar texto para evitar exceder cuotas
    if len(text) > max_chars:
        text = text[:max_chars]
        st.warning(f"El texto es muy largo, se truncó a {max_chars} caracteres para la extracción por IA.")
    
    prompt = f"""
    Eres un asistente experto en análisis de historias clínicas. A partir del siguiente texto, extrae toda la información relevante y devuélvela en formato JSON con la siguiente estructura:
    
    {{
        "paciente": {{
            "documento": "string",
            "nombre": "string",
            "fecha_nacimiento": "string (DD/MM/AAAA)",
            "edad": número,
            "telefono": "string",
            "direccion": "string"
        }},
        "servicios": [
            {{
                "sede_codigo": "string",
                "sede_nombre": "string",
                "fecha": "string (DD/MM/AAAA)",
                "hora": "string (HH:MM:SS)",
                "tipo_atencion": "string"
            }}
        ],
        "diagnosticos": [
            {{
                "codigo": "string (CIE-10)",
                "descripcion": "string"
            }}
        ],
        "medicamentos": [
            {{
                "cantidad": "string",
                "descripcion": "string",
                "dosis": "string",
                "frecuencia": "string",
                "via": "string",
                "estado": "string"
            }}
        ],
        "procedimientos": [
            {{
                "tipo": "quirurgico/no_quirurgico",
                "cantidad": "string",
                "descripcion": "string",
                "fecha": "string (opcional)",
                "hora": "string (opcional)"
            }}
        ],
        "cirugias": [
            {{
                "diagnostico_pre": "string",
                "diagnostico_post": "string",
                "anestesia": "string",
                "fecha": "string",
                "hora_inicio": "string",
                "hora_fin": "string",
                "descripcion": "string",
                "tejidos_patologia": "string",
                "participantes": [
                    {{
                        "codigo": "string",
                        "nombre": "string",
                        "tipo": "string",
                        "participo": "string"
                    }}
                ]
            }}
        ],
        "laboratorios": [
            {{
                "cantidad": "string",
                "descripcion": "string",
                "fecha": "string",
                "resultado": "string"
            }}
        ],
        "imagenes": [
            {{
                "cantidad": "string",
                "descripcion": "string",
                "fecha": "string",
                "resultado": "string"
            }}
        ],
        "interconsultas": [
            {{
                "especialidad": "string",
                "fecha_orden": "string"
            }}
        ],
        "evoluciones": [
            {{
                "fecha": "string",
                "texto": "string"
            }}
        ],
        "altas": [
            {{
                "fecha": "string",
                "info": "string"
            }}
        ]
    }}
    
    Si algún campo no se encuentra, déjalo vacío (null, lista vacía o string vacío según corresponda). Responde únicamente con el JSON, sin texto adicional.
    
    Texto de la historia clínica:
    {text}
    """
    
    try:
        response = model.generate_content(prompt)
        # Intentar parsear la respuesta como JSON
        # A veces Gemini incluye ```json ... ```, limpiar
        content = response.text
        # Extraer JSON si está en bloque de código
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        else:
            # Puede que ya sea JSON directamente
            pass
        data = json.loads(content)
        return data
    except json.JSONDecodeError:
        st.error("La respuesta de Gemini no es un JSON válido. Mostrando respuesta cruda:")
        st.code(content)
        return None
    except exceptions.ResourceExhausted as e:
        # Error 429 por cuota
        st.error(f"Límite de cuota excedido: {e}")
        # Intentar extraer tiempo de espera del mensaje de error
        retry_match = re.search(r'retry_delay \{ seconds: (\d+) \}', str(e))
        if retry_match:
            seconds = int(retry_match.group(1))
            st.info(f"Por favor, espera {seconds} segundos antes de reintentar.")
        return None
    except Exception as e:
        st.error(f"Error en la extracción con Gemini: {e}")
        return None

# ----------------------------------------------------------------------
# Procesamiento principal (elige método)
# ----------------------------------------------------------------------
def procesar_historia(texto, metodo="regex", api_key=None, model_name=None):
    texto = limpiar_texto(texto)
    if metodo == "regex":
        resultado = {
            'paciente': extraer_paciente(texto),
            'servicios': extraer_servicios(texto),
            'diagnosticos': extraer_diagnosticos(texto),
            'medicamentos': extraer_medicamentos(texto),
            'procedimientos': extraer_procedimientos(texto),
            'cirugias': extraer_cirugias(texto),
            'laboratorios': extraer_laboratorios(texto),
            'imagenes': extraer_imagenes(texto),
            'interconsultas': extraer_interconsultas(texto),
            'evoluciones': extraer_evoluciones(texto),
            'altas': extraer_altas(texto)
        }
        return resultado
    else:  # metodo == "ia"
        if not api_key:
            st.error("Se requiere API key para extracción por IA.")
            return None
        resultado = extract_with_gemini(texto, api_key, model_name)
        return resultado

# ----------------------------------------------------------------------
# Interfaz de Streamlit
# ----------------------------------------------------------------------
st.set_page_config(page_title="Lector HC con IA", page_icon="🩺", layout="wide")
st.title("🩺 Lector de Historias Clínicas + Análisis con Gemini AI")
st.markdown("Sube un archivo PDF de una historia clínica y obtén un reporte detallado. Luego puedes usar IA para analizar los datos.")

# Sidebar para configuración
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # Método de extracción
    extraction_method = st.radio(
        "Método de extracción",
        ["Reglas (rápido)", "IA (preciso, consume tokens)"],
        index=0,
        help="Con IA se usa Gemini para extraer la información; puede ser más lento y requiere API key."
    )
    
    # Configuración de IA (si se selecciona)
    if extraction_method == "IA (preciso, consume tokens)":
        st.subheader("🤖 Configuración de IA")
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("API key cargada desde secrets")
        else:
            api_key = st.text_input("Ingresa tu API key de Gemini", type="password")
            if not api_key:
                st.warning("Ingresa una API key para usar extracción por IA")
        
        # Modelos disponibles (incluyendo gemini-2.5-flash como opción, aunque puede no existir)
        model_options = {
            "Gemini 2.0 Flash": "gemini-2.0-flash",
            "Gemini 2.0 Flash Exp": "gemini-2.0-flash-exp",
            "Gemini 1.5 Flash": "gemini-1.5-flash",
            "Gemini 1.5 Pro": "gemini-1.5-pro",
            "Gemini 2.5 Flash (si disponible)": "gemini-2.5-flash"
        }
        selected_model = st.selectbox("Modelo", options=list(model_options.keys()))
        model_name = model_options[selected_model]
    else:
        api_key = None
        model_name = None

    st.markdown("---")
    st.markdown("**Nota:** Asegúrate de tener la librería instalada: `pip install google-generativeai`")

# Carga de archivo
MAX_MB = 200
archivo_subido = st.file_uploader("Selecciona un archivo PDF", type="pdf")

if archivo_subido is not None:
    tamaño_mb = archivo_subido.size / (1024 * 1024)
    if tamaño_mb > MAX_MB:
        st.error(f"El archivo excede el tamaño máximo de {MAX_MB} MB ({tamaño_mb:.2f} MB).")
    else:
        st.success(f"Archivo cargado: {archivo_subido.name} ({tamaño_mb:.2f} MB)")

        if st.button("🔍 Procesar PDF", type="primary"):
            with st.spinner("Extrayendo texto del PDF..."):
                texto, num_paginas = extraer_texto_pdf(archivo_subido)
                if texto is None:
                    st.stop()
                st.info(f"Se extrajeron {num_paginas} páginas.")

            with st.spinner("Analizando información..."):
                if extraction_method == "IA (preciso, consume tokens)" and (not api_key or not GEMINI_AVAILABLE):
                    st.error("No se puede usar extracción por IA: falta API key o librería.")
                    st.stop()
                metodo = "ia" if extraction_method == "IA (preciso, consume tokens)" else "regex"
                resultado = procesar_historia(texto, metodo=metodo, api_key=api_key, model_name=model_name)
                if resultado is None:
                    st.stop()
                st.session_state['resultado'] = resultado
                st.session_state['texto_crudo'] = texto

            st.success("✅ Extracción completada")

            # Mostrar datos del paciente y demás secciones (igual que antes)
            st.header("📋 Datos del paciente")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Nombre", resultado['paciente'].get('nombre', 'No encontrado'))
            with col2:
                st.metric("Documento", resultado['paciente'].get('documento', 'No encontrado'))
            with col3:
                st.metric("Edad", resultado['paciente'].get('edad', 'No encontrado'))
            with col4:
                st.metric("Teléfono", resultado['paciente'].get('telefono', 'No encontrado'))

            # ... (resto de secciones similar, omitidas por brevedad, pero deben incluirse)
            st.header(f"🏥 Servicios de atención ({len(resultado.get('servicios', []))})")
            # etc.

            # JSON completo
            st.header("📦 JSON completo")
            json_str = json.dumps(resultado, indent=2, ensure_ascii=False, default=str)
            st.download_button(
                label="📥 Descargar JSON",
                data=json_str,
                file_name=f"{archivo_subido.name.replace('.pdf', '')}_reporte_detallado.json",
                mime="application/json"
            )

# Sección de análisis con IA (igual que antes)
if 'resultado' in st.session_state and extraction_method == "Reglas (rápido)":  # Solo si no se usó IA para extraer
    st.markdown("---")
    st.header("🤖 Análisis con Inteligencia Artificial (Gemini)")
    
    if not GEMINI_AVAILABLE:
        st.error("La librería 'google-generativeai' no está instalada.")
    elif not api_key:
        st.warning("Ingresa una API key de Gemini en la barra lateral para usar esta función.")
    else:
        default_prompt = (
            "Actúa como un médico analizando una historia clínica. "
            "Resume los hallazgos más importantes: diagnósticos principales, medicamentos prescritos, "
            "procedimientos realizados, y cualquier evento relevante. "
            "Identifica posibles problemas de seguridad o interacciones medicamentosas si las hay. "
            "Proporciona un análisis estructurado."
        )
        user_prompt = st.text_area("✏️ Personaliza el prompt para la IA (opcional)", value=default_prompt, height=150)
        
        data_source = st.radio("Datos a enviar a la IA", 
                               ["Estructurados (JSON)", "Texto completo (puede ser largo)"],
                               index=0)
        data_format = "json" if data_source == "Estructurados (JSON)" else "text"
        
        if st.button("🚀 Analizar con IA", type="primary"):
            with st.spinner("Consultando a Gemini..."):
                try:
                    from google.api_core import exceptions
                    if data_format == "json":
                        data_to_send = st.session_state['resultado']
                    else:
                        data_to_send = st.session_state['texto_crudo']
                    
                    # Reutilizar función analyze_with_gemini (definida antes)
                    def analyze_with_gemini(data, prompt, api_key, model_name, data_format):
                        configure_gemini(api_key)
                        model = genai.GenerativeModel(model_name)
                        if data_format == "json":
                            data_str = json.dumps(data, indent=2, ensure_ascii=False, default=str)
                            full_prompt = f"{prompt}\n\nDatos extraídos de la historia clínica (formato JSON):\n{data_str}"
                        else:
                            full_prompt = f"{prompt}\n\nTexto completo de la historia clínica:\n{data[:100000]}"
                        response = model.generate_content(full_prompt)
                        return response.text
                    
                    response = analyze_with_gemini(
                        data=data_to_send,
                        prompt=user_prompt,
                        api_key=api_key,
                        model_name=model_name,
                        data_format=data_format
                    )
                    st.markdown("### Resultado del análisis")
                    st.write(response)
                    
                    st.download_button(
                        label="📥 Descargar análisis",
                        data=response,
                        file_name="analisis_gemini.txt",
                        mime="text/plain"
                    )
                except exceptions.ResourceExhausted as e:
                    st.error(f"Límite de cuota excedido: {e}")
                    retry_match = re.search(r'retry_delay \{ seconds: (\d+) \}', str(e))
                    if retry_match:
                        seconds = int(retry_match.group(1))
                        st.info(f"Por favor, espera {seconds} segundos antes de reintentar.")
                except Exception as e:
                    st.error(f"Error al comunicarse con Gemini: {e}")
