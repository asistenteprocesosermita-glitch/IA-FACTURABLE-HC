import streamlit as st
import PyPDF2
import re
import json
from datetime import datetime
import time
from fpdf import FPDF
import io

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

def formatear_fecha(fecha_str):
    """Convierte fecha DD/MM/AAAA a AAAA-MM-DD para ordenamiento."""
    try:
        return datetime.strptime(fecha_str, '%d/%m/%Y').date().isoformat()
    except:
        return fecha_str

# ----------------------------------------------------------------------
# Funciones de extracción mejoradas (completas)
# ----------------------------------------------------------------------
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
    """Extrae todos los registros de atención (ingresos a servicios)."""
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
    """Extrae diagnósticos con códigos CIE-10."""
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
    """
    Extrae medicamentos de:
    - FORMULA MEDICA ESTANDAR
    - CONCILIACIÓN MEDICAMENTOSA
    - PLAN TERAPEUTICO (listados de medicamentos)
    """
    medicamentos = []

    def procesar_linea_med(linea):
        partes = linea.strip().split(maxsplit=1)
        if len(partes) < 2:
            return None
        cantidad = partes[0] if re.match(r'^\d+\.?\d*$', partes[0]) else '1'
        desc = partes[1]
        dosis_match = re.search(r'(\d+[.,]?\d*\s*(?:MG|ML|G|MCG|UI))', desc, re.IGNORECASE)
        dosis = dosis_match.group(1) if dosis_match else ''
        return {
            'cantidad': cantidad,
            'descripcion': desc,
            'dosis': dosis,
            'frecuencia': '',
            'via': '',
            'estado': ''
        }

    # 1. Bloques FORMULA MEDICA ESTANDAR
    bloques_fm = re.split(r'FORMULA MEDICA ESTANDAR', texto)
    for bloque in bloques_fm[1:]:
        fin = re.search(r'\n[A-Z ]{5,}\n', bloque)
        if fin:
            bloque = bloque[:fin.start()]
        lineas = bloque.split('\n')
        i = 0
        while i < len(lineas):
            linea = lineas[i].strip()
            if not linea:
                i += 1
                continue
            if re.match(r'^\s*\d+\.?\d*\s+[A-Za-z0-9]', linea):
                med = procesar_linea_med(linea)
                if med:
                    for j in range(i, min(i+5, len(lineas))):
                        if 'Frecuencia' in lineas[j]:
                            med['frecuencia'] = lineas[j].strip()
                        if 'Via' in lineas[j]:
                            med['via'] = lineas[j].strip()
                        if 'Estado:' in lineas[j]:
                            med['estado'] = lineas[j].strip()
                    medicamentos.append(med)
            i += 1

    # 2. Bloques CONCILIACIÓN MEDICAMENTOSA
    bloques_conc = re.split(r'CONCILIACI[OÓ]N MEDICAMENTOSA', texto, re.IGNORECASE)
    for bloque in bloques_conc[1:]:
        fin = re.search(r'\n[A-Z ]{5,}\n', bloque)
        if fin:
            bloque = bloque[:fin.start()]
        lineas = bloque.split('\n')
        for linea in lineas:
            linea = linea.strip()
            if not linea:
                continue
            if re.search(r'\d+\s*(?:MG|ML|G|MCG)', linea, re.IGNORECASE):
                med = {
                    'cantidad': '1',
                    'descripcion': linea,
                    'dosis': '',
                    'frecuencia': '',
                    'via': '',
                    'estado': ''
                }
                dosis_match = re.search(r'(\d+[.,]?\d*\s*(?:MG|ML|G|MCG))', linea, re.IGNORECASE)
                if dosis_match:
                    med['dosis'] = dosis_match.group(1)
                via_match = re.search(r'\b(VO|IV|SC|IM|ORAL|INTRAVENOSO|SUBCUTANEA)\b', linea, re.IGNORECASE)
                if via_match:
                    med['via'] = via_match.group(1)
                freq_match = re.search(r'(CADA\s+\d+\s+HORAS|CADA\s+\d+H|CADA\s+\d+\s+DÍAS?|DIARIO|UNA\s+VEZ\s+AL\s+DÍA)', linea, re.IGNORECASE)
                if freq_match:
                    med['frecuencia'] = freq_match.group(1)
                medicamentos.append(med)

    # 3. PLAN - TERAPEUTICO (líneas con guiones)
    bloques_plan = re.split(r'PLAN\s*[-:]?\s*TERAPEUTICO', texto, re.IGNORECASE)
    for bloque in bloques_plan[1:]:
        fin = re.search(r'\n[A-Z ]{5,}\n', bloque)
        if fin:
            bloque = bloque[:fin.start()]
        lineas = bloque.split('\n')
        for linea in lineas:
            linea = linea.strip()
            if not linea or not linea.startswith('-'):
                continue
            linea = linea[1:].strip()
            if re.search(r'\d+\s*(?:MG|ML|G|MCG)', linea, re.IGNORECASE):
                med = {
                    'cantidad': '1',
                    'descripcion': linea,
                    'dosis': '',
                    'frecuencia': '',
                    'via': '',
                    'estado': ''
                }
                dosis_match = re.search(r'(\d+[.,]?\d*\s*(?:MG|ML|G|MCG))', linea, re.IGNORECASE)
                if dosis_match:
                    med['dosis'] = dosis_match.group(1)
                via_match = re.search(r'\b(VO|IV|SC|IM|ORAL|INTRAVENOSO|SUBCUTANEA)\b', linea, re.IGNORECASE)
                if via_match:
                    med['via'] = via_match.group(1)
                freq_match = re.search(r'(CADA\s+\d+\s+HORAS|CADA\s+\d+H|CADA\s+\d+\s+DÍAS?|DIARIO|UNA\s+VEZ\s+AL\s+DÍA)', linea, re.IGNORECASE)
                if freq_match:
                    med['frecuencia'] = freq_match.group(1)
                medicamentos.append(med)

    return medicamentos

def extraer_procedimientos(texto):
    """Extrae procedimientos quirúrgicos y no quirúrgicos con fechas."""
    procedimientos = []

    pattern_qx = r'PROCEDIMIENTOS QUIRURGICOS\s*\n\s*(\d+)\s+([^\n]+)'
    for match in re.finditer(pattern_qx, texto, re.IGNORECASE):
        procedimientos.append({
            'tipo': 'quirurgico',
            'cantidad': match.group(1).strip(),
            'descripcion': match.group(2).strip(),
            'fecha': None
        })

    pattern_noqx = r'ORDENES DE PROCEDIMIENTOS NO QX\s*\n\s*(\d+)\s+([^\n]+)'
    for match in re.finditer(pattern_noqx, texto, re.IGNORECASE):
        procedimientos.append({
            'tipo': 'no_quirurgico',
            'cantidad': match.group(1).strip(),
            'descripcion': match.group(2).strip(),
            'fecha': None
        })

    for proc in procedimientos:
        desc = proc['descripcion']
        idx = texto.find(desc)
        if idx != -1:
            ventana = texto[max(0, idx-200):idx+200]
            fecha_match = re.search(r'Fecha y Hora de Aplicación:(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})', ventana)
            if fecha_match:
                proc['fecha'] = fecha_match.group(1)
                proc['hora'] = fecha_match.group(2)

    return procedimientos

def extraer_cirugias(texto):
    """Extrae información detallada de cirugías (descripciones, participantes, etc.)"""
    cirugias = []
    patron = r'DESCRIPCION CIRUGIA.*?(?=\n[A-Z]{5,}\n|\Z)'
    for bloque in re.finditer(patron, texto, re.DOTALL | re.IGNORECASE):
        bloque_texto = bloque.group(0)
        cirugia = {}

        pre = re.search(r'Diagnostico Preoperatorio:\s*([^\n]+)', bloque_texto)
        if pre:
            cirugia['diagnostico_pre'] = pre.group(1).strip()
        post = re.search(r'Diagnostico Postoperatorio:\s*([^\n]+)', bloque_texto)
        if post:
            cirugia['diagnostico_post'] = post.group(1).strip()
        anest = re.search(r'Tipo de Anestesia:\s*([^\n]+)', bloque_texto)
        if anest:
            cirugia['anestesia'] = anest.group(1).strip()
        fecha = re.search(r'Realizacion Acto Quirurgico:\s*(\d{2}/\d{2}/\d{4})', bloque_texto)
        if fecha:
            cirugia['fecha'] = fecha.group(1)
        hora_inicio = re.search(r'Hora Inicio\s*(\d{2}:\d{2}:\d{2})', bloque_texto)
        if hora_inicio:
            cirugia['hora_inicio'] = hora_inicio.group(1)
        hora_fin = re.search(r'Hora Final\s*(\d{2}:\d{2}:\d{2})', bloque_texto)
        if hora_fin:
            cirugia['hora_fin'] = hora_fin.group(1)
        desc = re.search(r'Descripcion Quirurgica:\s*(.*?)(?=\nComplicacion:|\Z)', bloque_texto, re.DOTALL)
        if desc:
            cirugia['descripcion'] = desc.group(1).strip().replace('\n', ' ')
        tej = re.search(r'Tejidos enviados a patología\s*:\s*(.*?)(?=\n|$)', bloque_texto)
        if tej:
            cirugia['tejidos_patologia'] = tej.group(1).strip()
        participantes = re.findall(r'CÓDIGO\s+([^\n]+)\n\s*([^\n]+)\s+TIPO\s+([^\n]+)\s+PARTICIPO\?\s*([^\n]+)', bloque_texto)
        if participantes:
            cirugia['participantes'] = [{'codigo': p[0], 'nombre': p[1], 'tipo': p[2], 'participo': p[3]} for p in participantes]

        if cirugia:
            cirugias.append(cirugia)

    return cirugias

def extraer_laboratorios(texto):
    """Extrae órdenes de laboratorio y resultados."""
    laboratorios = []
    bloques = re.split(r'ORDENES DE LABORATORIO', texto)
    for bloque in bloques[1:]:
        fin = re.search(r'\n[A-Z ]{5,}\n', bloque)
        if fin:
            bloque = bloque[:fin.start()]
        lineas = bloque.split('\n')
        i = 0
        while i < len(lineas):
            linea = lineas[i].strip()
            if not linea:
                i += 1
                continue
            if re.match(r'^\s*\d+\s+[A-Za-z]', linea):
                partes = linea.split(maxsplit=1)
                if len(partes) == 2:
                    lab = {
                        'cantidad': partes[0].strip(),
                        'descripcion': partes[1].strip(),
                        'fecha': None,
                        'resultado': None
                    }
                    for j in range(i, min(i+5, len(lineas))):
                        if 'Fecha y Hora de Aplicación' in lineas[j]:
                            fecha_match = re.search(r'(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})', lineas[j])
                            if fecha_match:
                                lab['fecha'] = fecha_match.group(1)
                                lab['hora'] = fecha_match.group(2)
                        if 'Resultados:' in lineas[j]:
                            k = j+1
                            resultados = []
                            while k < len(lineas) and not re.match(r'^\s*\d+\s+[A-Za-z]', lineas[k]) and not re.match(r'\n[A-Z ]{5,}\n', lineas[k]):
                                res_linea = lineas[k].strip()
                                if res_linea:
                                    resultados.append(res_linea)
                                k += 1
                            if resultados:
                                lab['resultado'] = ' '.join(resultados)
                            break
                    laboratorios.append(lab)
            i += 1
    return laboratorios

def extraer_imagenes(texto):
    """Extrae órdenes de imágenes diagnósticas y sus informes."""
    imagenes = []
    bloques = re.split(r'ORDENES DE IMAGENES DIAGNOSTICAS', texto)
    for bloque in bloques[1:]:
        fin = re.search(r'\n[A-Z ]{5,}\n', bloque)
        if fin:
            bloque = bloque[:fin.start()]
        lineas = bloque.split('\n')
        i = 0
        while i < len(lineas):
            linea = lineas[i].strip()
            if not linea:
                i += 1
                continue
            if re.match(r'^\s*\d+\s+[A-Za-z]', linea):
                partes = linea.split(maxsplit=1)
                if len(partes) == 2:
                    img = {
                        'cantidad': partes[0].strip(),
                        'descripcion': partes[1].strip(),
                        'fecha': None,
                        'resultado': None
                    }
                    for j in range(i, min(i+5, len(lineas))):
                        if 'Fecha y Hora de Aplicación' in lineas[j]:
                            fecha_match = re.search(r'(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})', lineas[j])
                            if fecha_match:
                                img['fecha'] = fecha_match.group(1)
                                img['hora'] = fecha_match.group(2)
                        if 'Resultados:' in lineas[j]:
                            k = j+1
                            resultados = []
                            while k < len(lineas) and not re.match(r'^\s*\d+\s+[A-Za-z]', lineas[k]) and not re.match(r'\n[A-Z ]{5,}\n', lineas[k]):
                                res_linea = lineas[k].strip()
                                if res_linea:
                                    resultados.append(res_linea)
                                k += 1
                            if resultados:
                                img['resultado'] = ' '.join(resultados)
                            break
                    imagenes.append(img)
            i += 1
    return imagenes

def extraer_interconsultas(texto):
    """Extrae solicitudes de interconsulta."""
    interconsultas = []
    patron = r'INTERCONSULTA POR:\s*([^\n]+)\s+Fecha de Orden:\s*(\d{2}/\d{2}/\d{4})'
    for match in re.finditer(patron, texto, re.IGNORECASE):
        interconsultas.append({
            'especialidad': match.group(1).strip(),
            'fecha_orden': match.group(2).strip()
        })
    return interconsultas

def extraer_evoluciones(texto):
    """Extrae notas de evolución (fecha, médico, texto)"""
    evoluciones = []
    patron = r'EVOLUCION MEDICO\s*\n(.*?)(?=\n[A-Z ]{5,}\n|\Z)'
    for match in re.finditer(patron, texto, re.DOTALL | re.IGNORECASE):
        bloque = match.group(1).strip()
        fecha = re.search(r'(\d{2}/\d{2}/\d{4})', bloque)
        evoluciones.append({
            'fecha': fecha.group(1) if fecha else None,
            'texto': bloque
        })
    return evoluciones

def extraer_altas(texto):
    """Extrae información de alta médica."""
    altas = []
    patron = r'ALTA M[EÉ]DICA.*?(?=\n[A-Z ]{5,}\n|\Z)'
    for match in re.finditer(patron, texto, re.DOTALL | re.IGNORECASE):
        bloque = match.group(0)
        fecha = re.search(r'(\d{2}/\d{2}/\d{4})', bloque)
        altas.append({
            'fecha': fecha.group(1) if fecha else None,
            'info': bloque
        })
    return altas

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
        content = response.text
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        data = json.loads(content)
        return data
    except json.JSONDecodeError:
        st.error("La respuesta de Gemini no es un JSON válido. Mostrando respuesta cruda:")
        st.code(content)
        return None
    except exceptions.ResourceExhausted as e:
        st.error(f"Límite de cuota excedido: {e}")
        retry_match = re.search(r'retry_delay \{ seconds: (\d+) \}', str(e))
        if retry_match:
            seconds = int(retry_match.group(1))
            st.info(f"Por favor, espera {seconds} segundos antes de reintentar.")
        return None
    except Exception as e:
        st.error(f"Error en la extracción con Gemini: {e}")
        return None

# ----------------------------------------------------------------------
# Función para análisis con IA (genérico)
# ----------------------------------------------------------------------
def analyze_with_gemini(data, prompt, api_key, model_name, data_format="json"):
    """Envía datos a Gemini y retorna la respuesta textual."""
    configure_gemini(api_key)
    model = genai.GenerativeModel(model_name)
    if data_format == "json":
        data_str = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        full_prompt = f"{prompt}\n\nDatos extraídos de la historia clínica (formato JSON):\n{data_str}"
    else:
        # Texto completo truncado
        full_prompt = f"{prompt}\n\nTexto completo de la historia clínica:\n{data[:100000]}"
    response = model.generate_content(full_prompt)
    return response.text

# ----------------------------------------------------------------------
# Función para crear PDF del análisis
# ----------------------------------------------------------------------
def crear_pdf_analisis(texto_analisis, titulo="Análisis de Historia Clínica"):
    """Genera un PDF con el texto del análisis."""
    pdf = FPDF()
    pdf.add_page()
    # Usar fuente Helvetica con codificación latin-1 para caracteres acentuados
    pdf.set_font("Helvetica", size=12)
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Título
    pdf.set_font("Helvetica", 'B', 16)
    pdf.cell(200, 10, txt=titulo, ln=True, align='C')
    pdf.ln(10)
    
    # Fecha
    pdf.set_font("Helvetica", size=10)
    pdf.cell(200, 10, txt=f"Generado el: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", ln=True, align='R')
    pdf.ln(10)
    
    # Contenido
    pdf.set_font("Helvetica", size=12)
    # Dividir el texto en líneas y escribirlas
    for linea in texto_analisis.split('\n'):
        # Asegurar codificación
        try:
            pdf.multi_cell(0, 10, txt=linea.encode('latin-1', 'replace').decode('latin-1'))
        except:
            pdf.multi_cell(0, 10, txt=linea)
    
    # Devolver el PDF como bytes
    return pdf.output(dest='S').encode('latin-1')

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
    
    extraction_method = st.radio(
        "Método de extracción",
        ["Reglas (rápido)", "IA (preciso, consume tokens)"],
        index=0,
        help="Con IA se usa Gemini para extraer la información; puede ser más lento y requiere API key."
    )
    
    if extraction_method == "IA (preciso, consume tokens)":
        st.subheader("🤖 Configuración de IA")
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("API key cargada desde secrets")
        else:
            api_key = st.text_input("Ingresa tu API key de Gemini", type="password")
            if not api_key:
                st.warning("Ingresa una API key para usar extracción por IA")
        
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
    st.markdown("**Nota:** Asegúrate de tener la librería instalada: `pip install google-generativeai fpdf2`")

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

            # Mostrar datos del paciente
            st.header("📋 Datos del paciente")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Nombre", resultado.get('paciente', {}).get('nombre', 'No encontrado'))
            with col2:
                st.metric("Documento", resultado.get('paciente', {}).get('documento', 'No encontrado'))
            with col3:
                st.metric("Edad", resultado.get('paciente', {}).get('edad', 'No encontrado'))
            with col4:
                st.metric("Teléfono", resultado.get('paciente', {}).get('telefono', 'No encontrado'))

            # Servicios
            st.header(f"🏥 Servicios de atención ({len(resultado.get('servicios', []))})")
            for s in resultado.get('servicios', []):
                st.write(f"- **{s.get('tipo_atencion')}** en {s.get('sede_nombre')} ({s.get('fecha')} {s.get('hora')})")
            if not resultado.get('servicios'):
                st.write("No se encontraron servicios.")

            # Diagnósticos
            st.header(f"📌 Diagnósticos ({len(resultado.get('diagnosticos', []))})")
            for d in resultado.get('diagnosticos', []):
                st.write(f"- **{d.get('codigo')}** {d.get('descripcion')}")
            if not resultado.get('diagnosticos'):
                st.write("No se encontraron diagnósticos.")

            # Medicamentos (resumen)
            st.header(f"💊 Medicamentos ({len(resultado.get('medicamentos', []))})")
            for med in resultado.get('medicamentos', []):
                with st.expander(f"{med.get('descripcion', '')[:80]}..."):
                    st.write(f"**Cantidad:** {med.get('cantidad')}")
                    st.write(f"**Dosis:** {med.get('dosis')}")
                    st.write(f"**Vía:** {med.get('via')}")
                    st.write(f"**Frecuencia:** {med.get('frecuencia')}")
                    st.write(f"**Estado:** {med.get('estado')}")
            if not resultado.get('medicamentos'):
                st.write("No se encontraron medicamentos.")

            # Procedimientos
            st.header(f"🩺 Procedimientos ({len(resultado.get('procedimientos', []))})")
            for p in resultado.get('procedimientos', []):
                fecha = f" ({p.get('fecha')})" if p.get('fecha') else ""
                st.write(f"- **{p.get('descripcion')}** {fecha} – Cantidad: {p.get('cantidad')} ({p.get('tipo')})")
            if not resultado.get('procedimientos'):
                st.write("No se encontraron procedimientos.")

            # Cirugías detalladas
            st.header(f"🔪 Cirugías detalladas ({len(resultado.get('cirugias', []))})")
            for c in resultado.get('cirugias', []):
                with st.expander(f"Cirugía del {c.get('fecha', 'desconocida')}"):
                    st.write(f"**Diagnóstico preoperatorio:** {c.get('diagnostico_pre', 'N/A')}")
                    st.write(f"**Diagnóstico postoperatorio:** {c.get('diagnostico_post', 'N/A')}")
                    st.write(f"**Anestesia:** {c.get('anestesia', 'N/A')}")
                    st.write(f"**Hora inicio:** {c.get('hora_inicio', 'N/A')} – **Hora fin:** {c.get('hora_fin', 'N/A')}")
                    st.write(f"**Descripción:** {c.get('descripcion', 'N/A')}")
                    st.write(f"**Tejidos a patología:** {c.get('tejidos_patologia', 'N/A')}")
                    if 'participantes' in c:
                        st.write("**Participantes:**")
                        for part in c['participantes']:
                            st.write(f"  - {part.get('nombre')} ({part.get('tipo')})")
            if not resultado.get('cirugias'):
                st.write("No se encontraron descripciones quirúrgicas detalladas.")

            # Laboratorios
            st.header(f"🔬 Laboratorios ({len(resultado.get('laboratorios', []))})")
            for lab in resultado.get('laboratorios', []):
                fecha = f" ({lab.get('fecha')})" if lab.get('fecha') else ""
                with st.expander(f"{lab.get('descripcion')}{fecha}"):
                    st.write(f"**Cantidad:** {lab.get('cantidad')}")
                    if lab.get('resultado'):
                        st.write(f"**Resultado:** {lab.get('resultado')}")
            if not resultado.get('laboratorios'):
                st.write("No se encontraron órdenes de laboratorio.")

            # Imágenes
            st.header(f"📸 Imágenes diagnósticas ({len(resultado.get('imagenes', []))})")
            for img in resultado.get('imagenes', []):
                fecha = f" ({img.get('fecha')})" if img.get('fecha') else ""
                with st.expander(f"{img.get('descripcion')}{fecha}"):
                    st.write(f"**Cantidad:** {img.get('cantidad')}")
                    if img.get('resultado'):
                        st.write(f"**Resultado:** {img.get('resultado')}")
            if not resultado.get('imagenes'):
                st.write("No se encontraron imágenes.")

            # Interconsultas
            st.header(f"📞 Interconsultas ({len(resultado.get('interconsultas', []))})")
            for ic in resultado.get('interconsultas', []):
                st.write(f"- **{ic.get('especialidad')}** (orden: {ic.get('fecha_orden')})")
            if not resultado.get('interconsultas'):
                st.write("No se encontraron interconsultas.")

            # Evoluciones (resumen)
            st.header(f"📝 Evoluciones ({len(resultado.get('evoluciones', []))})")
            st.write(f"Se encontraron {len(resultado.get('evoluciones', []))} notas de evolución.")
            if not resultado.get('evoluciones'):
                st.write("No se encontraron notas de evolución.")

            # Altas
            st.header(f"🚪 Altas ({len(resultado.get('altas', []))})")
            for a in resultado.get('altas', []):
                st.write(f"- Alta del {a.get('fecha')}")
            if not resultado.get('altas'):
                st.write("No se encontraron registros de alta.")

            # JSON completo (opcional, pero lo dejamos para descarga)
            st.header("📦 JSON completo")
            json_str = json.dumps(resultado, indent=2, ensure_ascii=False, default=str)
            st.download_button(
                label="📥 Descargar JSON",
                data=json_str,
                file_name=f"{archivo_subido.name.replace('.pdf', '')}_reporte_detallado.json",
                mime="application/json"
            )

# Sección de análisis con IA (siempre visible si hay resultado)
if 'resultado' in st.session_state:
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
                    if data_format == "json":
                        data_to_send = st.session_state['resultado']
                    else:
                        data_to_send = st.session_state['texto_crudo']
                    
                    response = analyze_with_gemini(
                        data=data_to_send,
                        prompt=user_prompt,
                        api_key=api_key,
                        model_name=model_name,
                        data_format=data_format
                    )
                    
                    # Mostrar el análisis en un recuadro
                    st.markdown("### Resultado del análisis")
                    with st.container():
                        st.markdown(f"""
                        <div style="border: 2px solid #4CAF50; border-radius: 10px; padding: 15px; background-color: #f9f9f9;">
                            {response.replace(chr(10), '<br>')}
                        </div>
                        """, unsafe_allow_html=True)
                    
                    # Botón para descargar como PDF
                    pdf_bytes = crear_pdf_analisis(response, titulo="Análisis de Historia Clínica")
                    st.download_button(
                        label="📥 Descargar análisis como PDF",
                        data=pdf_bytes,
                        file_name=f"analisis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                        mime="application/pdf"
                    )
                    
                except exceptions.ResourceExhausted as e:
                    st.error(f"Límite de cuota excedido: {e}")
                    retry_match = re.search(r'retry_delay \{ seconds: (\d+) \}', str(e))
                    if retry_match:
                        seconds = int(retry_match.group(1))
                        st.info(f"Por favor, espera {seconds} segundos antes de reintentar.")
                except Exception as e:
                    st.error(f"Error al comunicarse con Gemini: {e}")
