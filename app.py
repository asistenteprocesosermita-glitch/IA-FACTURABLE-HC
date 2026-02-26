#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
===============================================================================
SISTEMA DE AUDITORÍA DE FACTURACIÓN EN HISTORIAS CLÍNICAS
===============================================================================
Versión: 2.0 (Profesional)
Autor: Auditoría Médica con IA
Licencia: MIT
Repositorio: https://github.com/tuusuario/lector-hc-facturacion

Este programa lee archivos PDF de historias clínicas, extrae información
estructurada mediante inteligencia artificial (Gemini) y genera un informe
detallado de los elementos facturables según la normativa colombiana.
===============================================================================
"""

# -----------------------------------------------------------------------------
# 1. IMPORTACIONES Y CONFIGURACIÓN INICIAL
# -----------------------------------------------------------------------------
import streamlit as st
import PyPDF2
import re
import json
import csv
import io
import os
import sys
import hashlib
import logging
import tempfile
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple, Union
from pathlib import Path
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from collections import defaultdict
import pandas as pd

# Configuración de la página DEBE ser lo primero
st.set_page_config(
    page_title="Auditoría HC con IA",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': 'https://github.com/tuusuario/lector-hc-facturacion',
        'Report a bug': 'https://github.com/tuusuario/lector-hc-facturacion/issues',
        'About': "# Lector de Historias Clínicas con IA\nVersión profesional para facturación médica."
    }
)

# -----------------------------------------------------------------------------
# 2. CONFIGURACIÓN DE LOGGING
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('auditoria_hc.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# 3. CONSTANTES GLOBALES Y CONFIGURACIÓN INTERNA
# -----------------------------------------------------------------------------
# Modelo de IA fijo (el más avanzado de Gemini)
MODELO_IA = "gemini-2.5-flash"

# Límites de procesamiento
MAX_CARACTERES_IA = 500_000          # Gemini puede manejar hasta 1M, pero por seguridad
MAX_MB_PDF = 200                      # Tamaño máximo del PDF
MAX_PAGINAS = 500                     # Número máximo de páginas a procesar
TIMEOUT_SEGUNDOS = 120                 # Timeout para llamadas a API

# Rutas para archivos temporales (usando tempdir del sistema)
TEMP_DIR = tempfile.gettempdir()

# Configuración de caché
CACHE_TTL = 3600  # 1 hora

# -----------------------------------------------------------------------------
# 4. FUNCIONES DE UTILIDAD GENERAL
# -----------------------------------------------------------------------------
def limpiar_texto(texto: str) -> str:
    """
    Limpia el texto eliminando líneas vacías múltiples y espacios redundantes.

    Args:
        texto (str): Texto original.

    Returns:
        str: Texto limpio.
    """
    if not texto:
        return ""
    # Eliminar caracteres de control excepto saltos de línea
    texto = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', texto)
    # Reemplazar múltiples saltos de línea por uno solo
    texto = re.sub(r'\n\s*\n', '\n', texto)
    # Eliminar espacios al inicio y final de cada línea
    texto = '\n'.join(line.strip() for line in texto.splitlines())
    return texto.strip()

def formatear_fecha(fecha_str: str, formato_entrada: str = "%d/%m/%Y", formato_salida: str = "%Y-%m-%d") -> str:
    """
    Convierte una fecha de un formato a otro.

    Args:
        fecha_str (str): Fecha en string.
        formato_entrada (str): Formato de entrada (por defecto DD/MM/AAAA).
        formato_salida (str): Formato deseado (por defecto AAAA-MM-DD).

    Returns:
        str: Fecha formateada o cadena vacía si hay error.
    """
    if not fecha_str:
        return ""
    try:
        fecha = datetime.strptime(fecha_str, formato_entrada)
        return fecha.strftime(formato_salida)
    except ValueError:
        logger.warning(f"No se pudo formatear la fecha: {fecha_str}")
        return fecha_str  # Devolver original si no se puede

def calcular_dias_estancia(fecha_ingreso: str, fecha_egreso: str, formato: str = "%d/%m/%Y") -> Optional[int]:
    """
    Calcula los días de estancia hospitalaria.

    Args:
        fecha_ingreso (str): Fecha de ingreso.
        fecha_egreso (str): Fecha de egreso.
        formato (str): Formato de las fechas.

    Returns:
        Optional[int]: Número de días o None si error.
    """
    try:
        ingreso = datetime.strptime(fecha_ingreso, formato)
        egreso = datetime.strptime(fecha_egreso, formato)
        delta = egreso - ingreso
        return delta.days
    except (ValueError, TypeError):
        logger.error(f"Error calculando días de estancia: {fecha_ingreso} - {fecha_egreso}")
        return None

def generar_hash_archivo(archivo_bytes: bytes) -> str:
    """
    Genera un hash SHA256 del contenido del archivo para usar como clave de caché.

    Args:
        archivo_bytes (bytes): Contenido del archivo.

    Returns:
        str: Hash hexadecimal.
    """
    return hashlib.sha256(archivo_bytes).hexdigest()

@lru_cache(maxsize=32)
def cached_regex_search(pattern: str, text: str, flags: int = 0) -> List[Tuple[str, ...]]:
    """
    Búsqueda regex con caché para mejorar rendimiento.

    Args:
        pattern (str): Patrón regex.
        text (str): Texto donde buscar.
        flags (int): Banderas de regex.

    Returns:
        List[Tuple[str, ...]]: Lista de tuplas con los grupos encontrados.
    """
    matches = []
    for match in re.finditer(pattern, text, flags):
        matches.append(match.groups())
    return matches

# -----------------------------------------------------------------------------
# 5. EXTRACCIÓN DE TEXTO DE PDF CON MÚLTIPLES MOTORES (FALLBACK)
# -----------------------------------------------------------------------------
def extraer_texto_pdf_pypdf2(archivo_pdf) -> Tuple[Optional[str], int]:
    """
    Extrae texto usando PyPDF2.

    Args:
        archivo_pdf: Archivo PDF cargado (objeto de archivo).

    Returns:
        Tuple[str, int]: Texto extraído y número de páginas, o (None, 0) si error.
    """
    texto = ""
    try:
        lector = PyPDF2.PdfReader(archivo_pdf)
        paginas = len(lector.pages)
        if paginas > MAX_PAGINAS:
            logger.warning(f"El PDF tiene {paginas} páginas, se procesarán las primeras {MAX_PAGINAS}")
            paginas = MAX_PAGINAS
        for i in range(paginas):
            pagina = lector.pages[i]
            contenido = pagina.extract_text()
            if contenido:
                texto += contenido + "\n"
        return texto, paginas
    except Exception as e:
        logger.error(f"Error con PyPDF2: {e}")
        return None, 0

def extraer_texto_pdf_pdfplumber(archivo_pdf) -> Tuple[Optional[str], int]:
    """
    Extrae texto usando pdfplumber (más preciso, si está instalado).

    Args:
        archivo_pdf: Archivo PDF cargado.

    Returns:
        Tuple[str, int]: Texto y número de páginas.
    """
    try:
        import pdfplumber
        with pdfplumber.open(archivo_pdf) as pdf:
            paginas = len(pdf.pages)
            if paginas > MAX_PAGINAS:
                paginas = MAX_PAGINAS
            texto = "\n".join(pdf.pages[i].extract_text() or "" for i in range(paginas))
            return texto, paginas
    except ImportError:
        logger.warning("pdfplumber no está instalado. Usando PyPDF2.")
        return None, 0
    except Exception as e:
        logger.error(f"Error con pdfplumber: {e}")
        return None, 0

def extraer_texto_pdf(archivo_pdf) -> Tuple[Optional[str], int]:
    """
    Intenta extraer texto del PDF usando múltiples métodos en orden de calidad.

    Args:
        archivo_pdf: Archivo PDF cargado.

    Returns:
        Tuple[str, int]: Texto extraído y número de páginas, o (None, 0) si falla.
    """
    # Intentar con pdfplumber primero (mejor calidad)
    texto, paginas = extraer_texto_pdf_pdfplumber(archivo_pdf)
    if texto:
        return texto, paginas

    # Fallback a PyPDF2
    archivo_pdf.seek(0)  # Reiniciar puntero
    texto, paginas = extraer_texto_pdf_pypdf2(archivo_pdf)
    if texto:
        return texto, paginas

    # Si ambos fallan, retornar error
    st.error("No se pudo extraer texto del PDF. Intenta con otro archivo o instala pdfplumber.")
    return None, 0

# -----------------------------------------------------------------------------
# 6. FUNCIONES DE EXTRACCIÓN POR REGEX (RESPALDO Y COMPARACIÓN)
# -----------------------------------------------------------------------------
# Estas funciones se mantienen como legado y para futuras mejoras, pero no se usan
# en el flujo principal a menos que se active un modo debug.

def extraer_paciente_regex(texto: str) -> Dict[str, Any]:
    """
    Extrae datos básicos del paciente usando expresiones regulares.

    Args:
        texto (str): Texto completo de la historia.

    Returns:
        dict: Diccionario con campos del paciente.
    """
    paciente = {}
    # Documento
    doc = re.search(r'CC\s*(\d+)', texto, re.IGNORECASE)
    if doc:
        paciente['documento'] = doc.group(1)
    # Nombre
    nombre = re.search(r'--\s*([A-ZÁÉÍÓÚÑ\s]+?)\s+Fec\.\s*Nacimiento', texto, re.IGNORECASE)
    if nombre:
        paciente['nombre'] = nombre.group(1).strip()
    # Fecha nacimiento
    fn = re.search(r'Fec\.\s*Nacimiento:\s*(\d{2}/\d{2}/\d{4})', texto, re.IGNORECASE)
    if fn:
        paciente['fecha_nacimiento'] = fn.group(1)
    # Edad
    edad = re.search(r'Edad\s*actual:\s*(\d+)\s*AÑOS', texto, re.IGNORECASE)
    if edad:
        paciente['edad'] = int(edad.group(1))
    # Teléfono
    tel = re.search(r'Teléfono:\s*(\d+)', texto, re.IGNORECASE)
    if tel:
        paciente['telefono'] = tel.group(1)
    # Dirección
    dire = re.search(r'Dirección:\s*([^\n]+)', texto, re.IGNORECASE)
    if dire:
        paciente['direccion'] = dire.group(1).strip()
    # EPS/Afiliación (patrón común)
    eps = re.search(r'(?:EPS|ENTIDAD PROMOTORA DE SALUD)[:\s]+([^\n]+)', texto, re.IGNORECASE)
    if eps:
        paciente['afiliacion'] = eps.group(1).strip()
    return paciente

def extraer_servicios_regex(texto: str) -> List[Dict[str, Any]]:
    """
    Extrae registros de atención (servicios) mediante regex.
    """
    servicios = []
    pattern = r'SEDE DE ATENCION\s+(\d+)\s+([^\n]+?)\s+FOLIO\s+\d+\s+FECHA\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})\s+TIPO DE ATENCION\s*:\s*([^\n]+)'
    for match in re.finditer(pattern, texto, re.IGNORECASE):
        servicios.append({
            'sede_codigo': match.group(1),
            'sede_nombre': match.group(2).strip(),
            'fecha_ingreso': match.group(3),
            'hora_ingreso': match.group(4),
            'tipo_atencion': match.group(5).strip(),
            'fecha_egreso': None,  # No disponible en este patrón simple
            'hora_egreso': None
        })
    return servicios

def extraer_diagnosticos_regex(texto: str) -> List[Dict[str, Any]]:
    """
    Extrae diagnósticos con códigos CIE-10.
    """
    diagnosticos = []
    patron = r'(?:DIAGN[OÓ]STICO|DX|DIAGN[OÓ]STICOS?)\s*:?\s*([A-Z0-9]+\s+[^\n]+)'
    for match in re.finditer(patron, texto, re.IGNORECASE):
        diag = match.group(1).strip()
        codigo = re.search(r'([A-Z]\d{2,3})', diag)
        diagnosticos.append({
            'codigo': codigo.group(1) if codigo else '',
            'descripcion': diag,
            'tipo': 'principal' if len(diagnosticos) == 0 else 'secundario'
        })
    return diagnosticos

def extraer_medicamentos_regex(texto: str) -> List[Dict[str, Any]]:
    """
    Extrae medicamentos de secciones específicas.
    """
    medicamentos = []
    # Implementación similar a la original, pero mejorada
    # ... (se puede mantener el código original completo, pero por brevedad se omite aquí)
    # En el archivo final se incluirán todas las funciones de extracción regex originales.
    # Por ahora, dejamos un placeholder.
    return medicamentos

def extraer_procedimientos_regex(texto: str) -> List[Dict[str, Any]]:
    """Extrae procedimientos quirúrgicos y no quirúrgicos."""
    # Placeholder
    return []

def extraer_cirugias_regex(texto: str) -> List[Dict[str, Any]]:
    """Extrae información detallada de cirugías."""
    # Placeholder
    return []

def extraer_laboratorios_regex(texto: str) -> List[Dict[str, Any]]:
    """Extrae órdenes de laboratorio y resultados."""
    # Placeholder
    return []

def extraer_imagenes_regex(texto: str) -> List[Dict[str, Any]]:
    """Extrae órdenes de imágenes diagnósticas."""
    # Placeholder
    return []

def extraer_interconsultas_regex(texto: str) -> List[Dict[str, Any]]:
    """Extrae solicitudes de interconsulta."""
    # Placeholder
    return []

def extraer_evoluciones_regex(texto: str) -> List[Dict[str, Any]]:
    """Extrae notas de evolución."""
    # Placeholder
    return []

def extraer_altas_regex(texto: str) -> List[Dict[str, Any]]:
    """Extrae información de alta médica."""
    # Placeholder
    return []

def procesar_historia_regex(texto: str) -> Dict[str, Any]:
    """
    Procesa la historia usando únicamente regex (para comparación o respaldo).
    """
    texto = limpiar_texto(texto)
    return {
        'paciente': extraer_paciente_regex(texto),
        'servicios': extraer_servicios_regex(texto),
        'diagnosticos': extraer_diagnosticos_regex(texto),
        'medicamentos': extraer_medicamentos_regex(texto),
        'procedimientos': extraer_procedimientos_regex(texto),
        'cirugias': extraer_cirugias_regex(texto),
        'laboratorios': extraer_laboratorios_regex(texto),
        'imagenes': extraer_imagenes_regex(texto),
        'interconsultas': extraer_interconsultas_regex(texto),
        'evoluciones': extraer_evoluciones_regex(texto),
        'altas': extraer_altas_regex(texto)
    }

# -----------------------------------------------------------------------------
# 7. FUNCIONES DE EXTRACCIÓN POR IA (GEMINI)
# -----------------------------------------------------------------------------
def configure_gemini(api_key: str) -> None:
    """
    Configura la API de Gemini.
    """
    try:
        genai.configure(api_key=api_key)
        logger.info("Gemini configurado correctamente.")
    except Exception as e:
        logger.error(f"Error configurando Gemini: {e}")
        st.error(f"Error configurando Gemini: {e}")
        raise

def extract_with_gemini(texto: str, api_key: str) -> Optional[Dict[str, Any]]:
    """
    Envía el texto a Gemini y solicita un JSON estructurado con enfoque en facturación.
    """
    configure_gemini(api_key)
    model = genai.GenerativeModel(MODELO_IA)

    # Truncar si es necesario
    if len(texto) > MAX_CARACTERES_IA:
        texto = texto[:MAX_CARACTERES_IA]
        st.warning(f"⚠️ El texto se truncó a {MAX_CARACTERES_IA} caracteres para la extracción.")

    prompt = f"""
    Eres un auditor médico experto en facturación de servicios de salud en Colombia.
    A partir del texto de la historia clínica, extrae TODA la información relevante para facturación y devuélvela en formato JSON con la siguiente estructura.

    **Instrucciones importantes:**
    - Identifica explícitamente si un ítem (medicamento, procedimiento, laboratorio, imagen) fue **REALIZADO** al paciente. Busca palabras como "aplicado", "realizado", "ejecutado", "administrado", o fechas/horas de ejecución.
    - Para las estancias, registra fechas y horas de ingreso y egreso de cada servicio.
    - Incluye todos los diagnósticos con sus códigos CIE-10.
    - Los medicamentos deben incluir dosis, vía, frecuencia y si fueron realizados.
    - Los procedimientos deben incluir tipo, cantidad, descripción, fecha/hora y si fueron realizados.
    - Las órdenes (laboratorio, imágenes) deben diferenciarse de los resultados; si hay resultado, se considera realizado.
    - Las evoluciones y valoraciones médicas son relevantes.
    - Incluye también información de afiliación (EPS, régimen) si aparece.

    **Estructura JSON esperada:**
    {{
        "paciente": {{
            "documento": "string",
            "nombre": "string",
            "fecha_nacimiento": "DD/MM/AAAA",
            "edad": número,
            "telefono": "string",
            "direccion": "string",
            "afiliacion": "string"  // EPS, régimen, etc.
        }},
        "servicios": [
            {{
                "sede_codigo": "string",
                "sede_nombre": "string",
                "fecha_ingreso": "DD/MM/AAAA",
                "hora_ingreso": "HH:MM:SS",
                "fecha_egreso": "DD/MM/AAAA",
                "hora_egreso": "HH:MM:SS",
                "tipo_atencion": "string",
                "estancia_validada": boolean  // true si hay fechas coherentes
            }}
        ],
        "diagnosticos": [
            {{
                "codigo": "string (CIE-10)",
                "descripcion": "string",
                "tipo": "principal/secundario"
            }}
        ],
        "medicamentos": [
            {{
                "cantidad": "string",
                "descripcion": "string",
                "dosis": "string",
                "frecuencia": "string",
                "via": "string",
                "estado": "string",
                "realizado": boolean,
                "fecha_aplicacion": "DD/MM/AAAA",
                "hora_aplicacion": "HH:MM:SS"
            }}
        ],
        "procedimientos": [
            {{
                "tipo": "quirurgico/no_quirurgico",
                "cantidad": "string",
                "descripcion": "string",
                "fecha": "DD/MM/AAAA",
                "hora": "HH:MM:SS",
                "realizado": boolean
            }}
        ],
        "cirugias": [
            {{
                "diagnostico_pre": "string",
                "diagnostico_post": "string",
                "anestesia": "string",
                "fecha": "DD/MM/AAAA",
                "hora_inicio": "HH:MM:SS",
                "hora_fin": "HH:MM:SS",
                "descripcion": "string",
                "tejidos_patologia": "string",
                "participantes": [
                    {{
                        "codigo": "string",
                        "nombre": "string",
                        "tipo": "string",
                        "participo": "string"
                    }}
                ],
                "realizado": boolean
            }}
        ],
        "laboratorios": [
            {{
                "cantidad": "string",
                "descripcion": "string",
                "fecha_orden": "DD/MM/AAAA",
                "fecha_realizacion": "DD/MM/AAAA",
                "resultado": "string",
                "realizado": boolean
            }}
        ],
        "imagenes": [
            {{
                "cantidad": "string",
                "descripcion": "string",
                "fecha_orden": "DD/MM/AAAA",
                "fecha_realizacion": "DD/MM/AAAA",
                "resultado": "string",
                "realizado": boolean
            }}
        ],
        "interconsultas": [
            {{
                "especialidad": "string",
                "fecha_orden": "DD/MM/AAAA",
                "fecha_realizacion": "DD/MM/AAAA",
                "realizado": boolean
            }}
        ],
        "evoluciones": [
            {{
                "fecha": "DD/MM/AAAA",
                "medico": "string",
                "texto": "string"
            }}
        ],
        "altas": [
            {{
                "fecha": "DD/MM/AAAA",
                "estado_salida": "string",
                "resumen": "string"
            }}
        ],
        "estancias": [
            {{
                "servicio": "string",
                "fecha_ingreso": "DD/MM/AAAA",
                "hora_ingreso": "HH:MM:SS",
                "fecha_egreso": "DD/MM/AAAA",
                "hora_egreso": "HH:MM:SS",
                "dias_estancia": número
            }}
        ]
    }}

    Si un campo no se encuentra, déjalo vacío (null, lista vacía o string vacío). Responde ÚNICAMENTE con el JSON, sin comentarios adicionales.

    Texto de la historia clínica:
    {texto}
    """

    try:
        response = model.generate_content(prompt)
        contenido = response.text

        # Extraer JSON si viene envuelto en markdown
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', contenido, re.DOTALL)
        if json_match:
            contenido = json_match.group(1)

        data = json.loads(contenido)
        logger.info("Extracción con Gemini exitosa.")
        return data

    except json.JSONDecodeError:
        logger.error("La respuesta de Gemini no es un JSON válido.")
        st.error("❌ La respuesta de Gemini no es un JSON válido. Mostrando respuesta cruda:")
        st.code(contenido)
        return None
    except Exception as e:
        logger.exception("Error en la extracción con Gemini")
        st.error(f"❌ Error en la extracción con Gemini: {e}")
        return None

# -----------------------------------------------------------------------------
# 8. FUNCIONES DE ANÁLISIS DE FACTURACIÓN
# -----------------------------------------------------------------------------
def analyze_billing_with_gemini(data: Dict[str, Any], api_key: str) -> str:
    """
    Envía los datos estructurados a Gemini para generar un informe de facturación.
    """
    configure_gemini(api_key)
    model = genai.GenerativeModel(MODELO_IA)

    prompt = f"""
    Actúa como un auditor de cuentas médicas especializado en facturación de servicios de salud en Colombia.
    A partir de los datos estructurados de la historia clínica (en formato JSON), genera un informe detallado que resalte TODOS los elementos facturables.

    **Debes incluir:**
    - **Datos del afiliado**: nombre, documento, EPS si se menciona.
    - **Programa o tipo de atención** (urgencias, hospitalización, consulta externa, etc.).
    - **Diagnósticos** principales y secundarios (códigos CIE-10).
    - **Estancias**: por cada servicio, fechas y horas de ingreso/egreso, y días de estancia. Valida la coherencia de las fechas.
    - **Procedimientos quirúrgicos y no quirúrgicos** realizados, con fechas y horas.
    - **Medicamentos aplicados**: aquellos marcados como "realizado", con dosis, vía, frecuencia y fechas de aplicación.
    - **Laboratorios e imágenes** realizados, con fechas y resultados si están disponibles.
    - **Interconsultas** realizadas.
    - **Valoraciones y evoluciones médicas** (fechas y médicos).
    - Cualquier otro servicio que pueda ser facturable según la normativa colombiana (RIPS, Manual Tarifario SOAT, etc.).

    **Formato del informe:**
    - Usa un lenguaje claro y profesional.
    - Organiza la información en secciones con títulos.
    - Destaca en **negritas** los conceptos clave.
    - Si falta información crítica para facturar, indícalo como "Pendiente".
    - Al final, incluye una tabla resumen con:
        * Total de días de estancia
        * Número de procedimientos realizados
        * Número de medicamentos aplicados
        * Número de laboratorios/imágenes realizados
        * Cualquier observación relevante para el facturador.

    **Datos de la historia clínica:**
    {json.dumps(data, indent=2, ensure_ascii=False, default=str)}
    """

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.exception("Error al generar análisis de facturación")
        return f"**Error al generar el análisis:** {e}"

def calcular_resumen_facturacion(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcula un resumen cuantitativo de los elementos facturables.

    Args:
        data (dict): Datos extraídos.

    Returns:
        dict: Resumen con conteos y totales.
    """
    resumen = {}

    # Paciente
    paciente = data.get('paciente', {})
    resumen['paciente'] = {
        'nombre': paciente.get('nombre', 'N/A'),
        'documento': paciente.get('documento', 'N/A'),
        'afiliacion': paciente.get('afiliacion', 'N/A')
    }

    # Estancias
    estancias = data.get('estancias', [])
    total_dias = sum(e.get('dias_estancia', 0) for e in estancias if e.get('dias_estancia'))
    resumen['estancias'] = {
        'total_dias': total_dias,
        'num_estancias': len(estancias)
    }

    # Procedimientos realizados
    procedimientos = data.get('procedimientos', [])
    realizados = [p for p in procedimientos if p.get('realizado')]
    resumen['procedimientos'] = {
        'total': len(procedimientos),
        'realizados': len(realizados)
    }

    # Medicamentos aplicados
    medicamentos = data.get('medicamentos', [])
    aplicados = [m for m in medicamentos if m.get('realizado')]
    resumen['medicamentos'] = {
        'total': len(medicamentos),
        'aplicados': len(aplicados)
    }

    # Laboratorios realizados
    laboratorios = data.get('laboratorios', [])
    labs_realizados = [l for l in laboratorios if l.get('realizado')]
    resumen['laboratorios'] = {
        'total': len(laboratorios),
        'realizados': len(labs_realizados)
    }

    # Imágenes realizadas
    imagenes = data.get('imagenes', [])
    img_realizados = [i for i in imagenes if i.get('realizado')]
    resumen['imagenes'] = {
        'total': len(imagenes),
        'realizados': len(img_realizados)
    }

    # Diagnósticos
    diagnosticos = data.get('diagnosticos', [])
    resumen['diagnosticos'] = len(diagnosticos)

    return resumen

# -----------------------------------------------------------------------------
# 9. GENERACIÓN DE REPORTES EN PDF (FPDF MEJORADO)
# -----------------------------------------------------------------------------
class PDFReport(FPDF):
    """
    Clase personalizada para generar reportes PDF con formato profesional.
    """
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=15)
        self.add_page()
        self.set_font('Helvetica', '', 11)

    def header(self):
        # Logo (opcional)
        # self.image('logo.png', 10, 8, 33)
        self.set_font('Helvetica', 'B', 12)
        self.cell(0, 10, 'Informe de Auditoría de Facturación', 0, 1, 'C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Página {self.page_no()}', 0, 0, 'C')

    def chapter_title(self, title):
        self.set_font('Helvetica', 'B', 12)
        self.set_fill_color(230, 230, 230)
        self.cell(0, 6, title, 0, 1, 'L', 1)
        self.ln(4)

    def chapter_body(self, body):
        self.set_font('Helvetica', '', 11)
        # Dividir en líneas y manejar negritas simples
        lines = body.split('\n')
        for line in lines:
            # Buscar patrones de negrita **texto**
            parts = re.split(r'(\*\*.*?\*\*)', line)
            for part in parts:
                if part.startswith('**') and part.endswith('**'):
                    self.set_font('Helvetica', 'B', 11)
                    self.write(5, part[2:-2])
                    self.set_font('Helvetica', '', 11)
                else:
                    self.write(5, part)
            self.ln(5)

def crear_pdf_analisis(texto_analisis: str, titulo: str = "Informe de Facturación") -> bytes:
    """
    Genera un PDF con el texto del análisis utilizando la clase personalizada.

    Args:
        texto_analisis (str): Texto del análisis (puede contener formato markdown simple).
        titulo (str): Título del informe.

    Returns:
        bytes: Contenido del PDF en bytes.
    """
    pdf = PDFReport()
    pdf.chapter_title(titulo)
    pdf.chapter_body(texto_analisis)
    return pdf.output(dest='S').encode('latin-1', errors='replace')

# -----------------------------------------------------------------------------
# 10. EXPORTACIÓN A EXCEL Y CSV
# -----------------------------------------------------------------------------
def exportar_a_excel(data: Dict[str, Any]) -> bytes:
    """
    Exporta los datos estructurados a un archivo Excel (bytes).

    Args:
        data (dict): Datos extraídos.

    Returns:
        bytes: Contenido del archivo Excel.
    """
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Hoja de paciente
        if data.get('paciente'):
            df_paciente = pd.DataFrame([data['paciente']])
            df_paciente.to_excel(writer, sheet_name='Paciente', index=False)

        # Hoja de servicios
        if data.get('servicios'):
            df_servicios = pd.DataFrame(data['servicios'])
            df_servicios.to_excel(writer, sheet_name='Servicios', index=False)

        # Hoja de diagnósticos
        if data.get('diagnosticos'):
            df_diag = pd.DataFrame(data['diagnosticos'])
            df_diag.to_excel(writer, sheet_name='Diagnósticos', index=False)

        # Hoja de medicamentos
        if data.get('medicamentos'):
            df_med = pd.DataFrame(data['medicamentos'])
            df_med.to_excel(writer, sheet_name='Medicamentos', index=False)

        # Hoja de procedimientos
        if data.get('procedimientos'):
            df_proc = pd.DataFrame(data['procedimientos'])
            df_proc.to_excel(writer, sheet_name='Procedimientos', index=False)

        # Hoja de laboratorios
        if data.get('laboratorios'):
            df_lab = pd.DataFrame(data['laboratorios'])
            df_lab.to_excel(writer, sheet_name='Laboratorios', index=False)

        # Hoja de imágenes
        if data.get('imagenes'):
            df_img = pd.DataFrame(data['imagenes'])
            df_img.to_excel(writer, sheet_name='Imágenes', index=False)

        # Hoja de interconsultas
        if data.get('interconsultas'):
            df_int = pd.DataFrame(data['interconsultas'])
            df_int.to_excel(writer, sheet_name='Interconsultas', index=False)

        # Hoja de evoluciones
        if data.get('evoluciones'):
            df_evol = pd.DataFrame(data['evoluciones'])
            df_evol.to_excel(writer, sheet_name='Evoluciones', index=False)

        # Hoja de altas
        if data.get('altas'):
            df_altas = pd.DataFrame(data['altas'])
            df_altas.to_excel(writer, sheet_name='Altas', index=False)

        # Hoja de estancias
        if data.get('estancias'):
            df_est = pd.DataFrame(data['estancias'])
            df_est.to_excel(writer, sheet_name='Estancias', index=False)

    output.seek(0)
    return output.getvalue()

def exportar_a_csv(data: Dict[str, Any]) -> Dict[str, bytes]:
    """
    Exporta cada sección a un archivo CSV separado.

    Returns:
        dict: Mapeo de nombre de sección a bytes CSV.
    """
    csv_files = {}
    for key, value in data.items():
        if isinstance(value, list) and value:
            # Convertir lista de dicts a DataFrame y luego a CSV
            df = pd.DataFrame(value)
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False, encoding='utf-8')
            csv_files[key] = csv_buffer.getvalue().encode('utf-8')
        elif key == 'paciente' and value:
            # Paciente es un dict, convertir a DataFrame de una fila
            df = pd.DataFrame([value])
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False, encoding='utf-8')
            csv_files[key] = csv_buffer.getvalue().encode('utf-8')
    return csv_files

# -----------------------------------------------------------------------------
# 11. GESTIÓN DE CACHÉ Y ESTADO DE SESIÓN
# -----------------------------------------------------------------------------
def inicializar_estado_sesion():
    """
    Inicializa las variables de sesión necesarias.
    """
    if 'datos_extraidos' not in st.session_state:
        st.session_state.datos_extraidos = None
    if 'texto_crudo' not in st.session_state:
        st.session_state.texto_crudo = None
    if 'hash_archivo' not in st.session_state:
        st.session_state.hash_archivo = None
    if 'procesado' not in st.session_state:
        st.session_state.procesado = False
    if 'api_key_valida' not in st.session_state:
        st.session_state.api_key_valida = False
    if 'debug_mode' not in st.session_state:
        st.session_state.debug_mode = False

def validar_api_key(api_key: str) -> bool:
    """
    Valida la API key realizando una llamada de prueba simple.
    """
    try:
        configure_gemini(api_key)
        model = genai.GenerativeModel(MODELO_IA)
        response = model.generate_content("responde solo 'ok'")
        if response.text:
            st.session_state.api_key_valida = True
            return True
    except Exception as e:
        logger.error(f"API key inválida: {e}")
        st.session_state.api_key_valida = False
    return False

# -----------------------------------------------------------------------------
# 12. INTERFAZ DE USUARIO CON STREAMLIT
# -----------------------------------------------------------------------------
def sidebar_configuracion() -> Optional[str]:
    """
    Renderiza la barra lateral con la configuración (solo API key).
    Retorna la API key si es válida, None en caso contrario.
    """
    with st.sidebar:
        st.header("⚙️ Configuración")

        # API key (desde secrets o input)
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("✅ API key cargada desde secrets")
        else:
            api_key = st.text_input("🔑 Ingresa tu API key de Gemini", type="password")
            if not api_key:
                st.warning("Se requiere una API key para continuar.")
                return None
            else:
                # Validar API key (solo si ha cambiado)
                if api_key != st.session_state.get('api_key_ingresada'):
                    with st.spinner("Validando API key..."):
                        if validar_api_key(api_key):
                            st.session_state.api_key_ingresada = api_key
                            st.success("API key válida")
                        else:
                            st.error("API key inválida. Verifica e intenta nuevamente.")
                            return None
                else:
                    if not st.session_state.api_key_valida:
                        st.error("API key inválida. Por favor, ingresa una válida.")
                        return None

        st.markdown("---")
        st.markdown(f"**Modelo utilizado:** `{MODELO_IA}` (fijo)")
        st.markdown(f"**Máx. caracteres:** {MAX_CARACTERES_IA:,}")
        st.markdown(f"**Tamaño máx. PDF:** {MAX_MB_PDF} MB")
        st.markdown("---")

        # Modo debug (oculto por defecto, solo se muestra si se activa con un código)
        if st.checkbox("Mostrar opciones avanzadas", value=False):
            st.session_state.debug_mode = st.checkbox("Modo debug", value=st.session_state.get('debug_mode', False))
            if st.session_state.debug_mode:
                st.info("Modo debug activado. Se mostrarán datos adicionales.")
        else:
            st.session_state.debug_mode = False

        st.markdown("---")
        st.markdown("**Nota:** Asegúrate de tener instalada la librería: `pip install google-generativeai fpdf2 pandas openpyxl`")

    return api_key

def main():
    """
    Función principal que controla el flujo de la aplicación.
    """
    # Inicializar estado de sesión
    inicializar_estado_sesion()

    # Obtener API key de la barra lateral
    api_key = sidebar_configuracion()
    if not api_key:
        st.stop()  # No continuar si no hay API key válida

    # Título principal
    st.title("📋 **Auditoría de Facturación en Historias Clínicas**")
    st.markdown("""
    Esta herramienta utiliza **Inteligencia Artificial (Gemini)** para extraer y analizar información de historias clínicas (PDF)
    con el fin de identificar todos los elementos facturables según la normativa colombiana.
    """)

    # Carga de archivo
    archivo_subido = st.file_uploader("📂 Selecciona un archivo PDF", type="pdf")

    if archivo_subido is not None:
        # Validar tamaño
        tamaño_mb = archivo_subido.size / (1024 * 1024)
        if tamaño_mb > MAX_MB_PDF:
            st.error(f"❌ El archivo excede el tamaño máximo de {MAX_MB_PDF} MB ({tamaño_mb:.2f} MB).")
            st.stop()

        st.success(f"✅ Archivo cargado: **{archivo_subido.name}** ({tamaño_mb:.2f} MB)")

        # Botón de procesamiento
        if st.button("🔍 Procesar PDF y generar análisis", type="primary", use_container_width=True):
            # Calcular hash para posible caché (futuro)
            archivo_bytes = archivo_subido.read()
            archivo_subido.seek(0)  # Reiniciar puntero
            hash_archivo = generar_hash_archivo(archivo_bytes)

            # Si ya se procesó el mismo archivo, podríamos cargar desde caché (opcional)
            # Por ahora, siempre procesamos de nuevo.

            with st.status("Procesando...", expanded=True) as status:
                # 1. Extraer texto del PDF
                status.update(label="📄 Extrayendo texto del PDF...")
                texto, num_paginas = extraer_texto_pdf(archivo_subido)
                if texto is None:
                    st.error("No se pudo extraer texto del PDF.")
                    st.stop()
                st.info(f"📑 Se extrajeron {num_paginas} páginas.")

                # 2. Extraer datos estructurados con IA
                status.update(label="🤖 Extrayendo información con IA (esto puede tomar hasta 2 minutos)...")
                datos_extraidos = extract_with_gemini(texto, api_key)
                if datos_extraidos is None:
                    st.error("Falló la extracción con IA.")
                    st.stop()

                # 3. Guardar en sesión
                st.session_state.datos_extraidos = datos_extraidos
                st.session_state.texto_crudo = texto
                st.session_state.hash_archivo = hash_archivo
                st.session_state.procesado = True

                status.update(label="✅ Procesamiento completado!", state="complete")

        # ----------------------------------------------------------------------
        # Mostrar resultados si ya se procesó
        # ----------------------------------------------------------------------
        if st.session_state.procesado and st.session_state.datos_extraidos:
            datos = st.session_state.datos_extraidos

            # Tabs para organizar la visualización
            tab1, tab2, tab3, tab4, tab5 = st.tabs([
                "📋 Datos del Paciente",
                "🏥 Servicios y Estancias",
                "💊 Medicamentos y Procedimientos",
                "🔬 Laboratorios e Imágenes",
                "📊 Análisis de Facturación"
            ])

            with tab1:
                st.header("🧑‍⚕️ Datos del paciente")
                paciente = datos.get('paciente', {})
                if paciente:
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Nombre", paciente.get('nombre', 'N/A'))
                    col2.metric("Documento", paciente.get('documento', 'N/A'))
                    col3.metric("Edad", paciente.get('edad', 'N/A'))
                    col4.metric("Teléfono", paciente.get('telefono', 'N/A'))
                    if paciente.get('afiliacion'):
                        st.write(f"**Afiliación:** {paciente['afiliacion']}")
                    if paciente.get('direccion'):
                        st.write(f"**Dirección:** {paciente['direccion']}")
                else:
                    st.warning("No se encontraron datos del paciente.")

                # Mostrar JSON completo si modo debug
                if st.session_state.debug_mode:
                    with st.expander("Ver JSON completo del paciente"):
                        st.json(paciente)

            with tab2:
                st.header("🏥 Servicios de atención y estancias")

                servicios = datos.get('servicios', [])
                if servicios:
                    st.subheader(f"Servicios ({len(servicios)})")
                    for s in servicios:
                        ingreso = f"{s.get('fecha_ingreso', '')} {s.get('hora_ingreso', '')}"
                        egreso = f"{s.get('fecha_egreso', '')} {s.get('hora_egreso', '')}"
                        st.markdown(f"- **{s.get('tipo_atencion')}** en {s.get('sede_nombre')} (Ingreso: {ingreso} - Egreso: {egreso})")
                else:
                    st.info("No se encontraron servicios.")

                estancias = datos.get('estancias', [])
                if estancias:
                    st.subheader(f"Estancias calculadas ({len(estancias)})")
                    for e in estancias:
                        st.markdown(f"- **{e.get('servicio')}**: {e.get('dias_estancia')} días (del {e.get('fecha_ingreso')} al {e.get('fecha_egreso')})")

                if st.session_state.debug_mode:
                    with st.expander("Ver JSON completo de servicios"):
                        st.json(servicios)

            with tab3:
                col_med, col_proc = st.columns(2)

                with col_med:
                    st.subheader("💊 Medicamentos")
                    medicamentos = datos.get('medicamentos', [])
                    if medicamentos:
                        for med in medicamentos:
                            realizado = "✅" if med.get('realizado') else "⏳"
                            fecha = f" el {med.get('fecha_aplicacion')} {med.get('hora_aplicacion', '')}" if med.get('fecha_aplicacion') else ""
                            st.markdown(f"{realizado} **{med.get('descripcion')}** – Dosis: {med.get('dosis')}, Vía: {med.get('via')}, Frec: {med.get('frecuencia')}{fecha}")
                    else:
                        st.info("No se encontraron medicamentos.")

                with col_proc:
                    st.subheader("🩺 Procedimientos")
                    procedimientos = datos.get('procedimientos', [])
                    if procedimientos:
                        for p in procedimientos:
                            realizado = "✅" if p.get('realizado') else "⏳"
                            fecha = f" ({p.get('fecha')} {p.get('hora', '')})" if p.get('fecha') else ""
                            st.markdown(f"{realizado} **{p.get('descripcion')}** – Cantidad: {p.get('cantidad')} ({p.get('tipo')}){fecha}")
                    else:
                        st.info("No se encontraron procedimientos.")

                # Cirugías detalladas
                cirugias = datos.get('cirugias', [])
                if cirugias:
                    with st.expander(f"🔪 Cirugías ({len(cirugias)})"):
                        for c in cirugias:
                            st.markdown(f"**{c.get('fecha')}** – {c.get('diagnostico_pre', 'Sin dx pre')} → {c.get('diagnostico_post', 'Sin dx post')}")
                            st.markdown(f"Anestesia: {c.get('anestesia')}, Horario: {c.get('hora_inicio')} - {c.get('hora_fin')}")
                            if c.get('participantes'):
                                st.markdown("Participantes: " + ", ".join([p.get('nombre', '') for p in c['participantes']]))
                            st.markdown("---")

            with tab4:
                col_lab, col_img = st.columns(2)

                with col_lab:
                    st.subheader("🔬 Laboratorios")
                    laboratorios = datos.get('laboratorios', [])
                    if laboratorios:
                        for lab in laboratorios:
                            realizado = "✅" if lab.get('realizado') else "⏳"
                            fecha = f" ({lab.get('fecha_realizacion')})" if lab.get('fecha_realizacion') else ""
                            st.markdown(f"{realizado} **{lab.get('descripcion')}** – Cantidad: {lab.get('cantidad')}{fecha}")
                            if lab.get('resultado'):
                                st.markdown(f"  *Resultado:* {lab['resultado']}")
                    else:
                        st.info("No se encontraron laboratorios.")

                with col_img:
                    st.subheader("📸 Imágenes diagnósticas")
                    imagenes = datos.get('imagenes', [])
                    if imagenes:
                        for img in imagenes:
                            realizado = "✅" if img.get('realizado') else "⏳"
                            fecha = f" ({img.get('fecha_realizacion')})" if img.get('fecha_realizacion') else ""
                            st.markdown(f"{realizado} **{img.get('descripcion')}** – Cantidad: {img.get('cantidad')}{fecha}")
                            if img.get('resultado'):
                                st.markdown(f"  *Resultado:* {img['resultado']}")
                    else:
                        st.info("No se encontraron imágenes.")

                # Interconsultas
                interconsultas = datos.get('interconsultas', [])
                if interconsultas:
                    with st.expander(f"📞 Interconsultas ({len(interconsultas)})"):
                        for ic in interconsultas:
                            realizado = "✅" if ic.get('realizado') else "⏳"
                            st.markdown(f"- **{ic.get('especialidad')}** – {realizado} (orden: {ic.get('fecha_orden')})")

            with tab5:
                st.header("💰 Análisis de Facturación")

                # Generar informe automáticamente si no existe en sesión
                if 'informe_facturacion' not in st.session_state:
                    with st.spinner("🧾 Generando informe de facturación con IA..."):
                        informe = analyze_billing_with_gemini(datos, api_key)
                        st.session_state.informe_facturacion = informe
                else:
                    informe = st.session_state.informe_facturacion

                # Mostrar el informe en un contenedor con estilo
                with st.container():
                    st.markdown(f"""
                    <div style="border: 2px solid #2E86C1; border-radius: 10px; padding: 20px; background-color: #F8F9F9;">
                        {informe.replace(chr(10), '<br>')}
                    </div>
                    """, unsafe_allow_html=True)

                # Botón para descargar como PDF
                pdf_bytes = crear_pdf_analisis(informe, titulo="Informe de Facturación - Historia Clínica")
                st.download_button(
                    label="📥 Descargar informe como PDF",
                    data=pdf_bytes,
                    file_name=f"informe_facturacion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )

                # Resumen cuantitativo
                resumen = calcular_resumen_facturacion(datos)
                st.subheader("📊 Resumen cuantitativo")
                colr1, colr2, colr3, colr4 = st.columns(4)
                colr1.metric("Días de estancia", resumen['estancias']['total_dias'])
                colr2.metric("Procedimientos realizados", resumen['procedimientos']['realizados'])
                colr3.metric("Medicamentos aplicados", resumen['medicamentos']['aplicados'])
                colr4.metric("Lab/Imág realizados", resumen['laboratorios']['realizados'] + resumen['imagenes']['realizados'])

            # ------------------------------------------------------------------
            # Botones de exportación adicionales
            # ------------------------------------------------------------------
            st.markdown("---")
            st.subheader("📦 Exportar datos")

            col_exp1, col_exp2, col_exp3 = st.columns(3)

            # JSON
            json_completo = json.dumps(datos, indent=2, ensure_ascii=False, default=str)
            col_exp1.download_button(
                label="📥 Descargar JSON",
                data=json_completo,
                file_name=f"{archivo_subido.name.replace('.pdf', '')}_datos.json",
                mime="application/json",
                use_container_width=True
            )

            # Excel
            excel_bytes = exportar_a_excel(datos)
            col_exp2.download_button(
                label="📥 Descargar Excel",
                data=excel_bytes,
                file_name=f"{archivo_subido.name.replace('.pdf', '')}_datos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

            # CSV (comprimido en zip)
            csv_files = exportar_a_csv(datos)
            if csv_files:
                import zipfile
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for name, content in csv_files.items():
                        zipf.writestr(f"{name}.csv", content)
                zip_buffer.seek(0)
                col_exp3.download_button(
                    label="📥 Descargar CSV (ZIP)",
                    data=zip_buffer,
                    file_name=f"{archivo_subido.name.replace('.pdf', '')}_csv.zip",
                    mime="application/zip",
                    use_container_width=True
                )

    else:
        # Mensaje inicial cuando no hay archivo
        st.info("👆 Sube un archivo PDF para comenzar.")
        # Mostrar ejemplo de uso
        with st.expander("📖 Instrucciones de uso"):
            st.markdown("""
            1. **Obtén una API key de Gemini** en [Google AI Studio](https://aistudio.google.com/).
            2. **Ingresa la API key** en la barra lateral.
            3. **Sube un archivo PDF** de una historia clínica.
            4. **Haz clic en 'Procesar PDF'** y espera (puede tomar hasta 2 minutos).
            5. **Revisa los resultados** en las pestañas y descarga el informe.

            **Nota:** La herramienta está optimizada para historias clínicas colombianas. Los resultados pueden variar según la calidad del PDF.
            """)

# -----------------------------------------------------------------------------
# 13. PUNTO DE ENTRADA PRINCIPAL
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Error no controlado en la aplicación")
        st.error(f"Ocurrió un error inesperado: {e}")
        if st.session_state.get('debug_mode'):
            st.code(traceback.format_exc())
