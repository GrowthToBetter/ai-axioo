import openai
import speech_recognition as sr
import pyttsx3
import json
import datetime
import re
import pandas as pd
from typing import Dict, List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from dataclasses import dataclass
from enum import Enum
import threading
import queue
import os
import logging
from flask import Flask, request, jsonify
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import requests
import base64
from io import BytesIO
import tempfile
import wave
import audioop
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv
from flask_cors import CORS

# Load file .env
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Konfigurasi API Keys
openai.api_key = os.getenv("OPENAI_API_KEY")
client = openai.OpenAI(api_key=openai.api_key)

# Konfigurasi Twilio untuk WhatsApp
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Konfigurasi Supabase PostgreSQL
def parse_database_url(database_url: str) -> Dict[str, str]:
    """Parse DATABASE_URL into connection parameters"""
    parsed = urlparse(database_url)
    return {
        'host': parsed.hostname,
        'port': parsed.port or 5432,
        'database': parsed.path[1:].split('?')[0],  # Remove leading '/' and query params
        'user': parsed.username,
        'password': unquote(parsed.password) if parsed.password else None
    }

# Use DATABASE_URL if available, otherwise fall back to individual parameters
DATABASE_URL = os.getenv('DATABASE_URL')
DIRECT_URL = os.getenv('DIRECT_URL')

# Parse the database URL
if DATABASE_URL:
    DB_CONFIG = parse_database_url(DATABASE_URL)
else:
    # Fallback to individual environment variables
    DB_CONFIG = {
        'host': os.getenv('DB_HOST'),
        'port': int(os.getenv('DB_PORT')),
        'database': os.getenv('DB_NAME'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD')
    }

class EmergencyType(Enum):
    KEBAKARAN = "kebakaran"
    BANJIR = "banjir"
    GEMPA = "gempa"
    KECELAKAAN = "kecelakaan"
    MEDIS = "medis"
    HEWAN_BERBAHAYA = "hewan_berbahaya"
    POHON_TUMBANG = "pohon_tumbang"
    LAINNYA = "lainnya"

class UrgencyLevel(Enum):
    KRITIS = 5  # Nyawa terancam
    TINGGI = 4  # Properti/lingkungan terancam
    SEDANG = 3  # Risiko sedang
    RENDAH = 2  # Tidak mendesak
    INFORMASI = 1  # Hanya laporan

class ReportStatus(Enum):
    BARU = "BARU"
    DITINDAKLANJUTI = "DITINDAKLANJUTI"
    SEDANG_PROSES = "SEDANG_PROSES"
    SELESAI = "SELESAI"
    DIBATALKAN = "DIBATALKAN"

@dataclass
class EmergencyReport:
    id: str
    timestamp: datetime.datetime
    caller_info: str
    caller_phone: str
    location: str
    emergency_type: EmergencyType
    urgency_level: UrgencyLevel
    description: str
    structured_data: Dict
    ai_recommendations: List[str]
    status: ReportStatus = ReportStatus.BARU
    voice_file_path: Optional[str] = None
    response_sent: bool = False

class SupabasePostgreSQLManager:
    def __init__(self):
        self.connection_pool = []
        self.create_tables()
    
    def get_connection(self):
        try:
            # Add SSL settings for Supabase
            conn_params = DB_CONFIG.copy()
            conn_params['sslmode'] = 'require'
            
            conn = psycopg2.connect(**conn_params)
            logger.info("Successfully connected to Supabase PostgreSQL")
            return conn
        except Exception as e:
            logger.error(f"Supabase database connection error: {e}")
            logger.error(f"Connection config: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
            return None
    
    def create_tables(self):
            
        """Membuat tabel-tabel yang diperlukan di Supabase"""
        # Modified for Supabase - use gen_random_uuid() instead of uuid_generate_v4()
        create_tables_sql = """
        -- Create extension if not exists (Supabase usually has this enabled)
        CREATE SCHEMA IF NOT EXISTS dev_moklet_ai;
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
        
        -- Emergency reports table
        CREATE TABLE IF NOT EXISTS dev_moklet_ai.emergency_reports (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            report_number VARCHAR(50) UNIQUE NOT NULL,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            caller_info TEXT,
            caller_phone VARCHAR(20),
            location TEXT,
            emergency_type VARCHAR(50),
            urgency_level INTEGER,
            description TEXT,
            structured_data JSONB,
            ai_recommendations JSONB,
            status VARCHAR(20) DEFAULT 'BARU',
            voice_file_path TEXT,
            response_sent BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- WhatsApp conversations table
        CREATE TABLE IF NOT EXISTS dev_moklet_ai.whatsapp_conversations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            phone_number VARCHAR(20) NOT NULL,
            message_sid VARCHAR(100),
            message_body TEXT,
            message_type VARCHAR(20),
            media_url TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            report_id UUID REFERENCES dev_moklet_ai.emergency_reports(id)
        );
        
        -- Create indexes for better performance
        CREATE INDEX IF NOT EXISTS idx_emergency_reports_timestamp 
            ON dev_moklet_ai.emergency_reports(timestamp);
        CREATE INDEX IF NOT EXISTS idx_emergency_reports_status 
            ON dev_moklet_ai.emergency_reports(status);
        CREATE INDEX IF NOT EXISTS idx_emergency_reports_urgency 
            ON dev_moklet_ai.emergency_reports(urgency_level);
        CREATE INDEX IF NOT EXISTS idx_whatsapp_phone 
            ON dev_moklet_ai.whatsapp_conversations(phone_number);
        """
        
        conn = self.get_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(create_tables_sql)
                conn.commit()
                logger.info("Supabase database tables created successfully")
            except Exception as e:
                logger.error(f"Error creating tables in Supabase: {e}")
                conn.rollback()
            finally:
                conn.close()
        else:
            logger.error("Failed to connect to Supabase for table creation")

class EnhancedEmergencyNLPSystem:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.tts_engine = pyttsx3.init()
        self.db_manager = SupabasePostgreSQLManager()  # Updated to use Supabase manager
        self.audio_queue = queue.Queue()
        self.flask_app = Flask(__name__)
        self.setup_flask_routes()
        
        # Setup TTS voice
        self.setup_tts()
        
        # Setup speech recognizer
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        self.recognizer.operation_timeout = None
        self.recognizer.phrase_threshold = 0.3
        self.recognizer.non_speaking_duration = 0.8

    def setup_tts(self):
        """Setup Text-to-Speech engine"""
        try:
            voices = self.tts_engine.getProperty('voices')
            for voice in voices:
                if 'indonesia' in voice.name.lower() or 'id' in voice.id.lower():
                    self.tts_engine.setProperty('voice', voice.id)
                    break
            
            self.tts_engine.setProperty('rate', 150)
            self.tts_engine.setProperty('volume', 0.9)
        except Exception as e:
            logger.warning(f"TTS setup warning: {e}")

    def setup_flask_routes(self):
        """Setup Flask routes untuk WhatsApp webhook"""
        @self.flask_app.route('/webhook/whatsapp', methods=['POST'])
        def whatsapp_webhook():
            return self.handle_whatsapp_message()
        
        @self.flask_app.route('/webhook/whatsapp/status', methods=['POST'])
        def whatsapp_status():
            return self.handle_whatsapp_status()
        
        @self.flask_app.route('/api/reports', methods=['GET'])
        def get_reports():
            return self.get_reports_api()
        
        @self.flask_app.route('/api/reports/<report_id>', methods=['GET'])
        def get_report_detail(report_id):
            return self.get_report_detail_api(report_id)
        
        @self.flask_app.route('/health', methods=['GET'])
        def health_check():
            return jsonify({"status": "healthy", "database": "supabase", "timestamp": datetime.datetime.now().isoformat()})

    def enhanced_speech_to_text(self, audio_file_path: str = None, audio_data = None) -> Optional[str]:
        """Enhanced speech recognition dengan multiple fallback"""
        methods = [
            ('Google', self.recognize_google_enhanced),
            ('Whisper', self.recognize_whisper_enhanced),
        ]
        
        for method_name, method_func in methods:
            try:
                logger.info(f"Trying {method_name} speech recognition...")
                if audio_file_path:
                    with sr.AudioFile(audio_file_path) as source:
                        audio = self.recognizer.record(source)
                else:
                    audio = audio_data
                
                text = method_func(audio)
                if text:
                    logger.info(f"Successfully transcribed using {method_name}: {text[:100]}...")
                    return text
            except Exception as e:
                logger.warning(f"{method_name} failed: {e}")
                continue
        
        return None

    def recognize_google_enhanced(self, audio) -> Optional[str]:
        """Enhanced Google Speech Recognition"""
        try:
            return self.recognizer.recognize_google(
                audio, 
                language="id-ID",
                show_all=False
            )
        except Exception as e:
            logger.error(f"Google recognition error: {e}")
            return None

    def recognize_whisper_enhanced(self, audio) -> Optional[str]:
        """Whisper-based recognition using OpenAI API"""
        try:
            # Convert audio to temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                with wave.open(tmp_file.name, 'wb') as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(audio.sample_width)
                    wav_file.setframerate(audio.frame_rate)
                    wav_file.writeframes(audio.frame_data)
                
                # Use OpenAI Whisper API
                with open(tmp_file.name, 'rb') as audio_file:
                    response = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language="id"
                    )
                
                os.unlink(tmp_file.name)
                return response.text
        except Exception as e:
            logger.error(f"Whisper recognition error: {e}")
            return None

    def listen_to_audio_enhanced(self) -> Optional[str]:
        """Enhanced audio listening dengan noise reduction"""
        try:
            print("🎤 Mendengarkan... (Tekan Ctrl+C untuk berhenti)")
            with self.microphone as source:
                # Advanced noise adjustment
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
                print("🔄 Siap mendengar...")
                
                audio = self.recognizer.listen(
                    source, 
                    timeout=30,
                    phrase_time_limit=30
                )
            
            print("🔄 Memproses audio...")
            text = self.enhanced_speech_to_text(audio_data=audio)
            
            if text:
                print(f"📝 Teks terdeteksi: {text}")
                return text
            else:
                print("❌ Tidak dapat memproses audio")
                return None
                
        except sr.WaitTimeoutError:
            print("⏰ Timeout - tidak ada suara terdeteksi")
            return None
        except Exception as e:
            logger.error(f"Audio listening error: {e}")
            return None

    def process_whatsapp_voice(self, media_url: str) -> Optional[str]:
        """Process voice message dari WhatsApp"""
        try:
            # Download voice file
            response = requests.get(media_url)
            if response.status_code != 200:
                return None
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
                tmp_file.write(response.content)
                temp_path = tmp_file.name
            
            # Convert OGG to WAV jika diperlukan
            wav_path = temp_path.replace(".ogg", ".wav")
            try:
                # Untuk konversi format, bisa menggunakan pydub
                # from pydub import AudioSegment
                # audio = AudioSegment.from_ogg(temp_path)
                # audio.export(wav_path, format="wav")
                
                # Fallback: langsung gunakan file asli
                text = self.enhanced_speech_to_text(audio_file_path=temp_path)
                
                # Cleanup
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                if os.path.exists(wav_path) and wav_path != temp_path:
                    os.unlink(wav_path)
                
                return text
                
            except Exception as e:
                logger.error(f"Voice processing error: {e}")
                return None
                
        except Exception as e:
            logger.error(f"WhatsApp voice processing error: {e}")
            return None

    def extract_emergency_info_enhanced(self, text: str, context: Dict = None) -> Dict:
        """Enhanced emergency information extraction"""
        system_prompt = """
        Anda adalah AI sistem tanggap darurat instansi publik Indonesia yang canggih.
        
        Analisis laporan darurat dengan detail dan berikan respons JSON dengan struktur:
        {
            "emergency_type": "kebakaran|banjir|gempa|kecelakaan|medis|hewan_berbahaya|pohon_tumbang|lainnya",
            "urgency_level": 1-5 (1=informasi, 2=rendah, 3=sedang, 4=tinggi, 5=kritis),
            "location": {
                "raw_location": "lokasi yang disebutkan",
                "estimated_address": "alamat perkiraan lebih spesifik",
                "landmarks": ["patokan terdekat"]
            },
            "incident_details": {
                "what_happened": "deskripsi kejadian",
                "when": "waktu kejadian jika disebutkan",
                "scale": "skala kejadian (kecil/sedang/besar)",
                "cause": "penyebab jika diketahui"
            },
            "victims_info": {
                "count": "jumlah korban",
                "condition": "kondisi korban",
                "ages": "rentang usia jika disebutkan",
                "special_needs": "kebutuhan khusus"
            },
            "immediate_actions": [
                "tindakan yang harus dilakukan pelapor sekarang juga"
            ],
            "required_resources": [
                "unit/sumber daya yang dibutuhkan"
            ],
            "safety_instructions": [
                "instruksi keselamatan spesifik"
            ],
            "additional_info": {
                "contact_info": "info kontak pelapor jika disebutkan",
                "accessibility": "kondisi akses lokasi",
                "weather_impact": "pengaruh cuaca jika relevan"
            }
        }
        
        Berikan analisis yang detail dan akurat berdasarkan konteks Indonesia.
        """
        
        context_info = ""
        if context:
            context_info = f"\nKonteks tambahan: {json.dumps(context, ensure_ascii=False)}"
        
        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Laporan darurat: {text}{context_info}"}
                ],
                temperature=0.2,
                max_tokens=1200
            )
            
            result = response.choices[0].message.content.strip()
            if result.startswith("```json"):
                result = result.replace("```json", "").replace("```", "").strip()
            
            return json.loads(result)
        except Exception as e:
            logger.error(f"Error extracting emergency info: {e}")
            return self.get_fallback_response_enhanced()

    def get_fallback_response_enhanced(self) -> Dict:
        """Enhanced fallback response"""
        return {
            "emergency_type": "lainnya",
            "urgency_level": 3,
            "location": {
                "raw_location": "tidak spesifik",
                "estimated_address": "perlu klarifikasi",
                "landmarks": []
            },
            "incident_details": {
                "what_happened": "perlu klarifikasi lebih lanjut",
                "when": "tidak disebutkan",
                "scale": "tidak diketahui",
                "cause": "tidak diketahui"
            },
            "victims_info": {
                "count": "tidak diketahui",
                "condition": "tidak diketahui",
                "ages": "tidak disebutkan",
                "special_needs": "tidak diketahui"
            },
            "immediate_actions": ["tetap tenang", "jauhi bahaya", "tunggu bantuan"],
            "required_resources": ["unit standar emergency"],
            "safety_instructions": ["pastikan keselamatan pribadi", "hindari area berbahaya"],
            "additional_info": {
                "contact_info": "tidak disebutkan",
                "accessibility": "tidak diketahui",
                "weather_impact": "tidak relevan"
            }
        }

    def save_report_to_postgresql(self, report: EmergencyReport) -> bool:
        """Save report ke Supabase PostgreSQL"""
        conn = self.db_manager.get_connection()
        if not conn:
            return False
        
        try:
            with conn.cursor() as cursor:
                insert_query = """
                INSERT INTO dev_moklet_ai.emergency_reports (
                    report_number, caller_info, caller_phone, location,
                    emergency_type, urgency_level, description, structured_data,
                    ai_recommendations, status, voice_file_path, response_sent
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """
                
                cursor.execute(insert_query, (
                    report.id,
                    report.caller_info,
                    report.caller_phone,
                    report.location,
                    report.emergency_type.value,
                    report.urgency_level.value,
                    report.description,
                    json.dumps(report.structured_data, ensure_ascii=False),
                    json.dumps(report.ai_recommendations, ensure_ascii=False),
                    report.status.value,
                    report.voice_file_path,
                    report.response_sent
                ))
                
                report_uuid = cursor.fetchone()[0]
                conn.commit()
                
                logger.info(f"Report {report.id} saved to Supabase with UUID: {report_uuid}")
                return True
                
        except Exception as e:
            logger.error(f"Error saving report to Supabase: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def save_whatsapp_conversation(self, phone_number: str, message_body: str, 
                                 message_sid: str, message_type: str = "text",
                                 media_url: str = None, report_id: str = None):
        """Save WhatsApp conversation to Supabase database"""
        conn = self.db_manager.get_connection()
        if not conn:
            return
        
        try:
            with conn.cursor() as cursor:
                # Get report UUID if report_id provided
                report_uuid = None
                if report_id:
                    cursor.execute("SELECT id FROM dev_moklet_ai.emergency_reports WHERE report_number = %s", (report_id,))
                    result = cursor.fetchone()
                    if result:
                        report_uuid = result[0]
                
                insert_query = """
                INSERT INTO dev_moklet_ai.whatsapp_conversations (
                    phone_number, message_sid, message_body, message_type,
                    media_url, report_id
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """
                
                cursor.execute(insert_query, (
                    phone_number,
                    message_sid,
                    message_body,
                    message_type,
                    media_url,
                    report_uuid
                ))
                
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error saving WhatsApp conversation to Supabase: {e}")
        finally:
            conn.close()

    def send_whatsapp_message(self, to_number: str, message: str) -> bool:
        """Send WhatsApp message via Twilio"""
        try:
            message = twilio_client.messages.create(
                body=message,
                from_=TWILIO_WHATSAPP_NUMBER,
                to=f"whatsapp:{to_number}"
            )
            
            logger.info(f"WhatsApp message sent to {to_number}: {message.sid}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {e}")
            return False

    def handle_whatsapp_message(self):
        """Handle incoming WhatsApp messages with comprehensive emergency situation guidance"""
        try:
            # Get message data
            message_sid = request.form.get('MessageSid')
            from_number = request.form.get('From', '').replace('whatsapp:', '')
            message_body = request.form.get('Body', '')
            media_url = request.form.get('MediaUrl0')
            media_content_type = request.form.get('MediaContentType0', '')

            logger.info(f"WhatsApp message from {from_number}: {message_body[:100]}")

            # Process voice message with improved handling
            if media_url and 'audio' in media_content_type:
                logger.info("Processing voice message...")

                try:
                    # Download voice file with proper authentication
                    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                    response = requests.get(media_url, auth=auth, timeout=30)

                    if response.status_code == 200:
                        # Create temporary files for processing
                        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
                            ogg_file.write(response.content)
                            ogg_path = ogg_file.name

                        # Convert OGG to WAV using OpenAI Whisper API directly
                        try:
                            # Use OpenAI Whisper API which can handle OGG files directly
                            with open(ogg_path, 'rb') as audio_file:
                                transcription = client.audio.transcriptions.create(
                                    model="whisper-1",
                                    file=audio_file,
                                    language="id"  # Indonesian language
                                )

                            transcribed_text = transcription.text

                            if transcribed_text and transcribed_text.strip():
                                message_body = transcribed_text
                                message_type = "voice"
                                logger.info(f"Voice message transcribed successfully: {transcribed_text[:100]}...")
                            else:
                                logger.warning("Voice transcription returned empty text")
                                response_msg = "Maaf, tidak dapat memproses pesan suara. Silakan kirim pesan teks atau coba lagi."
                                self.send_whatsapp_message(from_number, response_msg)
                                return str(MessagingResponse())

                        except Exception as whisper_error:
                            logger.error(f"Whisper transcription error: {whisper_error}")
                            response_msg = "Maaf, tidak dapat memproses pesan suara. Silakan kirim pesan teks atau coba lagi."
                            self.send_whatsapp_message(from_number, response_msg)
                            return str(MessagingResponse())

                        finally:
                            # Clean up temporary file
                            if os.path.exists(ogg_path):
                                try:
                                    os.unlink(ogg_path)
                                except Exception as e:
                                    logger.warning(f"Failed to delete temp file {ogg_path}: {e}")

                    else:
                        logger.error(f"Failed to download voice message: HTTP {response.status_code}")
                        response_msg = "Maaf, gagal mengunduh pesan suara. Silakan coba kirim ulang atau gunakan pesan teks."
                        self.send_whatsapp_message(from_number, response_msg)
                        return str(MessagingResponse())

                except requests.exceptions.RequestException as e:
                    logger.error(f"Error downloading voice message: {e}")
                    response_msg = "Maaf, terjadi kesalahan saat memproses pesan suara. Silakan kirim pesan teks."
                    self.send_whatsapp_message(from_number, response_msg)
                    return str(MessagingResponse())

            else:
                message_type = "text"

            # Process emergency report only if we have message content
            if message_body and message_body.strip():
                context = {
                    "source": "whatsapp",
                    "phone_number": from_number,
                    "message_type": message_type
                }

                # Create emergency report
                extracted_info = self.extract_emergency_info_enhanced(message_body, context)

                report_id = f"WA{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

                report = EmergencyReport(
                    id=report_id,
                    timestamp=datetime.datetime.now(),
                    caller_info=f"WhatsApp: {from_number}",
                    caller_phone=from_number,
                    location=extracted_info.get('location', {}).get('raw_location', 'Tidak diketahui'),
                    emergency_type=EmergencyType(extracted_info.get('emergency_type', 'lainnya')),
                    urgency_level=UrgencyLevel(extracted_info.get('urgency_level', 3)),
                    description=message_body,
                    structured_data=extracted_info,
                    ai_recommendations=extracted_info.get('immediate_actions', []),
                    voice_file_path=media_url if message_type == "voice" else None
                )

                # Save to database
                success = self.save_report_to_postgresql(report)
                self.save_whatsapp_conversation(
                    from_number, message_body, message_sid, 
                    message_type, media_url, report_id
                )

                # Generate comprehensive step-by-step response
                response_text = self.generate_comprehensive_emergency_response(
                    extracted_info, message_body, report_id, from_number
                )

                # Send response in parts if too long
                self.send_comprehensive_response_parts(from_number, response_text, report_id)

                # Update response sent status
                if success:
                    self.update_report_response_status(report_id, True)

            else:
                # Handle empty message case
                logger.warning(f"Empty message received from {from_number}")
                response_msg = "Maaf, pesan kosong diterima. Silakan kirim laporan darurat Anda dengan detail yang jelas."
                self.send_whatsapp_message(from_number, response_msg)

            return str(MessagingResponse())

        except Exception as e:
            logger.error(f"Error handling WhatsApp message: {e}")
            error_response = "Terjadi kesalahan sistem. Tim teknis akan segera menindaklanjuti laporan Anda."
            try:
                # Try to get phone number for error response
                from_number = request.form.get('From', '').replace('whatsapp:', '')
                if from_number:
                    self.send_whatsapp_message(from_number, error_response)
            except Exception as send_error:
                logger.error(f"Failed to send error response: {send_error}")

            return str(MessagingResponse().message(error_response))

    def generate_comprehensive_emergency_response(self, extracted_info: Dict, original_text: str, report_id: str, phone_number: str) -> Dict:
        """Generate comprehensive step-by-step emergency response using GPT-4"""

        system_prompt = """
        Anda adalah operator darurat profesional Indonesia yang sangat berpengalaman. 

        Tugas Anda adalah memberikan panduan darurat yang KOMPREHENSIF dan STEP-BY-STEP untuk situasi yang dilaporkan.

        Berikan respons dalam format JSON dengan struktur berikut:
        {
            "immediate_response": "respons segera singkat untuk menenangkan pelapor (maks 100 karakter)",
            "situation_assessment": "penilaian situasi berdasarkan laporan",
            "immediate_safety_steps": [
                "langkah keselamatan segera yang harus dilakukan SEKARANG (step-by-step, maksimal 5 langkah)"
            ],
            "detailed_instructions": {
                "do_immediately": [
                    "tindakan yang harus dilakukan segera (detail dan spesifik)"
                ],
                "do_not_do": [
                    "hal-hal yang TIDAK boleh dilakukan (untuk keselamatan)"
                ],
                "if_situation_worsens": [
                    "langkah-langkah jika situasi memburuk"
                ]
            },
            "medical_first_aid": [
                "panduan pertolongan pertama jika ada korban (step-by-step)"
            ],
            "contact_emergency_services": {
                "when_to_call": "kapan harus menghubungi layanan darurat",
                "what_to_tell_them": "informasi yang harus disampaikan ke petugas",
                "numbers": ["112", "nomor layanan darurat spesifik lainnya"]
            },
            "evacuation_guidance": {
                "should_evacuate": "apakah perlu evakuasi (ya/tidak/tergantung)",
                "evacuation_route": "rute evakuasi yang disarankan",
                "safe_meeting_point": "titik kumpul yang aman",
                "what_to_bring": ["barang-barang penting yang harus dibawa"]
            },
            "communication_plan": {
                "inform_family": "cara menginformasikan keluarga/orang terdekat",
                "stay_connected": "cara tetap terhubung dengan tim darurat",
                "updates": "bagaimana mendapatkan update situasi"
            },
            "psychological_support": [
                "cara menenangkan diri dan orang lain di lokasi"
            ],
            "follow_up_actions": [
                "tindakan lanjutan setelah situasi darurat mereda"
            ],
            "prevention_tips": [
                "tips mencegah kejadian serupa di masa depan"
            ]
        }

        PENTING:
        - Gunakan bahasa Indonesia yang jelas dan mudah dipahami
        - Berikan instruksi yang spesifik dan actionable
        - Pertimbangkan kondisi Indonesia (geografis, infrastruktur, budaya)
        - Prioritaskan keselamatan jiwa di atas segalanya
        - Berikan instruksi yang realistis dan dapat dilakukan oleh masyarakat umum
        - Sesuaikan dengan tingkat urgensi dan jenis darurat yang dilaporkan
        """

        urgency_level = extracted_info.get('urgency_level', 3)
        emergency_type = extracted_info.get('emergency_type', 'lainnya')
        location_info = extracted_info.get('location', {})
        victims_info = extracted_info.get('victims_info', {})
        incident_details = extracted_info.get('incident_details', {})

        user_prompt = f"""
        LAPORAN DARURAT:
        {original_text}

        ANALISIS SISTEM:
        - Jenis Darurat: {emergency_type}
        - Tingkat Urgensi: {urgency_level}/5
        - Lokasi: {location_info.get('raw_location', 'tidak diketahui')}
        - Landmark: {location_info.get('landmarks', [])}
        - Korban: {victims_info.get('count', 'tidak diketahui')} orang
        - Kondisi Korban: {victims_info.get('condition', 'tidak diketahui')}
        - Skala Kejadian: {incident_details.get('scale', 'tidak diketahui')}
        - Waktu Kejadian: {incident_details.get('when', 'tidak disebutkan')}
        - Penyebab: {incident_details.get('cause', 'tidak diketahui')}

        Nomor Laporan: {report_id}
        Nomor Telepon Pelapor: {phone_number}

        Berikan panduan darurat yang SANGAT KOMPREHENSIF dan STEP-BY-STEP untuk situasi ini.
        """

        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,  # Low temperature for consistent, reliable responses
                max_tokens=2000
            )

            result = response.choices[0].message.content.strip()
            if result.startswith("```json"):
                result = result.replace("```json", "").replace("```", "").strip()

            comprehensive_response = json.loads(result)

            # Add report metadata
            comprehensive_response["report_metadata"] = {
                "report_id": report_id,
                "timestamp": datetime.datetime.now().isoformat(),
                "emergency_type": emergency_type,
                "urgency_level": urgency_level,
                "phone_number": phone_number
            }

            return comprehensive_response

        except Exception as e:
            logger.error(f"Error generating comprehensive emergency response: {e}")
            return self.get_fallback_comprehensive_response(report_id, emergency_type, urgency_level)

    def get_fallback_comprehensive_response(self, report_id: str, emergency_type: str, urgency_level: int) -> Dict:
        """Fallback comprehensive response ketika GPT gagal"""
        return {
            "immediate_response": "Laporan darurat diterima. Tim akan segera menindaklanjuti. Tetap tenang dan ikuti panduan ini.",
            "situation_assessment": f"Situasi darurat {emergency_type} dengan tingkat urgensi {urgency_level}/5 telah dilaporkan dan sedang dianalisis.",
            "immediate_safety_steps": [
                "Tetap tenang dan jangan panik",
                "Pastikan keselamatan pribadi terlebih dahulu",
                "Jauhi area berbahaya jika memungkinkan",
                "Hubungi 112 jika situasi mengancam jiwa",
                "Ikuti instruksi dari petugas jika sudah ada di lokasi"
            ],
            "detailed_instructions": {
                "do_immediately": [
                    "Lakukan penilaian cepat terhadap bahaya di sekitar",
                    "Bantu korban jika aman untuk dilakukan",
                    "Amankan area dari bahaya tambahan",
                    "Siapkan jalur evakuasi jika diperlukan"
                ],
                "do_not_do": [
                    "Jangan panik atau bertindak ceroboh",
                    "Jangan mendekati area yang tidak aman",
                    "Jangan memindahkan korban kecuali dalam bahaya langsung",
                    "Jangan meninggalkan lokasi kecuali untuk mencari bantuan"
                ],
                "if_situation_worsens": [
                    "Segera hubungi 112",
                    "Evakuasi ke tempat yang aman",
                    "Berikan informasi lokasi yang jelas ke petugas",
                    "Tetap tenang dan ikuti arahan petugas darurat"
                ]
            },
            "medical_first_aid": [
                "Periksa kesadaran dan pernapasan korban",
                "Jika tidak bernapas, lakukan CPR jika terlatih",
                "Hentikan pendarahan dengan menekan luka",
                "Jaga korban tetap hangat dan tenang",
                "Jangan berikan makanan atau minuman"
            ],
            "contact_emergency_services": {
                "when_to_call": "Segera jika ada korban jiwa atau bahaya serius",
                "what_to_tell_them": "Lokasi, jenis darurat, jumlah korban, kondisi saat ini",
                "numbers": ["112", "113 (Pemadam Kebakaran)", "118 (Ambulans)", "110 (Polisi)"]
            },
            "evacuation_guidance": {
                "should_evacuate": "Evaluasi berdasarkan tingkat bahaya",
                "evacuation_route": "Gunakan rute terdekat dan teraman",
                "safe_meeting_point": "Tempat terbuka jauh dari bahaya",
                "what_to_bring": ["Dokumen penting", "Obat-obatan", "Air dan makanan darurat", "Senter/ponsel"]
            },
            "communication_plan": {
                "inform_family": "Hubungi keluarga untuk memberi tahu lokasi dan kondisi",
                "stay_connected": "Simpan nomor ini untuk update: " + report_id,
                "updates": "Pantau media sosial dan radio lokal untuk informasi terbaru"
            },
            "psychological_support": [
                "Tetap tenang dengan bernapas dalam-dalam",
                "Fokus pada tindakan yang dapat dilakukan",
                "Berbicara dengan tenang kepada orang lain",
                "Ingat bahwa bantuan sedang dalam perjalanan"
            ],
            "follow_up_actions": [
                "Laporkan kerusakan kepada pihak berwenang",
                "Dokumentasikan kejadian untuk asuransi",
                "Cari dukungan psikologis jika diperlukan",
                "Pelajari langkah pencegahan untuk masa depan"
            ],
            "prevention_tips": [
                "Siapkan kit darurat di rumah",
                "Pelajari rute evakuasi di sekitar tempat tinggal",
                "Ikuti pelatihan pertolongan pertama",
                "Pastikan nomor darurat mudah diakses"
            ],
            "report_metadata": {
                "report_id": report_id,
                "timestamp": datetime.datetime.now().isoformat(),
                "emergency_type": emergency_type,
                "urgency_level": urgency_level
            }
        }

    def send_comprehensive_response_parts(self, phone_number: str, comprehensive_response: Dict, report_id: str):
        """Send comprehensive response in multiple parts to avoid WhatsApp message length limits"""

        try:
            # Part 1: Immediate Response and Assessment
            part1 = f"""🚨 RESPON DARURAT - {report_id}

    {comprehensive_response['immediate_response']}

    📋 PENILAIAN SITUASI:
    {comprehensive_response['situation_assessment']}

    ⚡ LANGKAH KESELAMATAN SEGERA:"""

            for i, step in enumerate(comprehensive_response['immediate_safety_steps'], 1):
                part1 += f"\n{i}. {step}"

            self.send_whatsapp_message(phone_number, part1)

            # Small delay between messages
            import time
            time.sleep(1)

            # Part 2: Detailed Instructions
            part2 = f"""📝 PANDUAN DETAIL - {report_id}

    ✅ LAKUKAN SEGERA:"""
            for i, action in enumerate(comprehensive_response['detailed_instructions']['do_immediately'], 1):
                part2 += f"\n{i}. {action}"

            part2 += f"\n\n❌ JANGAN LAKUKAN:"
            for i, dont in enumerate(comprehensive_response['detailed_instructions']['do_not_do'], 1):
                part2 += f"\n{i}. {dont}"

            self.send_whatsapp_message(phone_number, part2)
            time.sleep(1)

            # Part 3: Medical and Emergency Contacts
            part3 = f"""🏥 PERTOLONGAN PERTAMA - {report_id}

    JIKA ADA KORBAN:"""
            for i, aid in enumerate(comprehensive_response['medical_first_aid'], 1):
                part3 += f"\n{i}. {aid}"

            contact_info = comprehensive_response['contact_emergency_services']
            part3 += f"\n\n📞 HUBUNGI DARURAT:"
            part3 += f"\n• Kapan: {contact_info['when_to_call']}"
            part3 += f"\n• Nomor: {', '.join(contact_info['numbers'])}"
            part3 += f"\n• Sampaikan: {contact_info['what_to_tell_them']}"

            self.send_whatsapp_message(phone_number, part3)
            time.sleep(1)

            # Part 4: Evacuation and Communication
            evac_info = comprehensive_response['evacuation_guidance']
            comm_info = comprehensive_response['communication_plan']

            part4 = f"""🚪 PANDUAN EVAKUASI - {report_id}

    Perlu Evakuasi: {evac_info['should_evacuate']}
    Rute: {evac_info['evacuation_route']}
    Titik Kumpul: {evac_info['safe_meeting_point']}

    Bawa: {', '.join(evac_info['what_to_bring'])}

    📱 KOMUNIKASI:
    • Keluarga: {comm_info['inform_family']}
    • Update: {comm_info['updates']}"""

            self.send_whatsapp_message(phone_number, part4)
            time.sleep(1)

            # Part 5: Psychological Support and Follow-up
            part5 = f"""🧠 DUKUNGAN PSIKOLOGIS - {report_id}

    TETAP TENANG:"""
            for i, support in enumerate(comprehensive_response['psychological_support'], 1):
                part5 += f"\n{i}. {support}"

            part5 += f"\n\nTINDAKAN LANJUTAN:"
            for i, followup in enumerate(comprehensive_response['follow_up_actions'][:3], 1):
                part5 += f"\n{i}. {followup}"

            part5 += f"\n\n📋 No. Laporan: {report_id}"
            part5 += f"\n🚨 Darurat: 112"
            part5 += f"\n⏰ {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}"

            self.send_whatsapp_message(phone_number, part5)

            # Optional: Send prevention tips as final message after delay
            time.sleep(2)
            prevention_msg = f"💡 TIPS PENCEGAHAN - {report_id}\n\n"
            for i, tip in enumerate(comprehensive_response['prevention_tips'][:3], 1):
                prevention_msg += f"{i}. {tip}\n"

            prevention_msg += "\nTim darurat telah menerima laporan Anda dan akan segera menindaklanjuti. Tetap aman! 🙏"

            self.send_whatsapp_message(phone_number, prevention_msg)

            logger.info(f"Comprehensive emergency response sent to {phone_number} for report {report_id}")

        except Exception as e:
            logger.error(f"Error sending comprehensive response parts: {e}")
            # Send fallback single message
            fallback_msg = f"""🚨 DARURAT - {report_id}

    Laporan Anda telah diterima dan sedang ditindaklanjuti.

    LANGKAH SEGERA:
    1. Tetap tenang dan aman
    2. Hubungi 112 jika kritis
    3. Ikuti panduan keselamatan umum
    4. Tunggu tim darurat

    No. Laporan: {report_id}
    Darurat: 112"""

            self.send_whatsapp_message(phone_number, fallback_msg)
    def generate_whatsapp_response(self, extracted_info: Dict, original_text: str, report_id: str) -> str:
        """Generate response untuk WhatsApp"""
        system_prompt = """
        Anda adalah operator darurat profesional via WhatsApp.
        Berikan respons yang:
        1. Singkat tapi informatif (maksimal 160 karakter untuk SMS, 300 untuk WhatsApp)
        2. Menenangkan dan memberikan arahan jelas
        3. Menyertakan nomor laporan
        4. Menggunakan emoji yang tepat untuk WhatsApp
        5. Bahasa Indonesia yang mudah dipahami
        """
        
        urgency = extracted_info.get('urgency_level', 3)
        emergency_type = extracted_info.get('emergency_type', 'lainnya')
        location = extracted_info.get('location', {}).get('raw_location', 'lokasi Anda')
        
        prompt = f"""
        Info darurat:
        - Jenis: {emergency_type}
        - Urgensi: {urgency}/5
        - Lokasi: {location}
        - Nomor laporan: {report_id}
        - Instruksi: {extracted_info.get('immediate_actions', [])}
        
        Buat respons WhatsApp yang tepat untuk situasi ini.
        """
        
        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=150
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Add report ID dan emergency contact
            final_response = f"{ai_response}\n\n📝 No. Laporan: {report_id}\n🚨 Darurat: 112"
            
            return final_response
            
        except Exception as e:
            logger.error(f"Error generating WhatsApp response: {e}")
            return f"✅ Laporan darurat diterima!\n📝 No: {report_id}\nTim akan segera menindaklanjuti.\n🚨 Darurat: 112"

    def update_report_response_status(self, report_id: str, response_sent: bool):
        """Update status response sent"""
        conn = self.db_manager.get_connection()
        if not conn:
            return
        
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE dev_moklet_ai.emergency_reports SET response_sent = %s, updated_at = CURRENT_TIMESTAMP WHERE report_number = %s",
                    (response_sent, report_id)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Error updating response status: {e}")
        finally:
            conn.close()

    def handle_whatsapp_status(self):
        """Handle WhatsApp delivery status"""
        message_sid = request.form.get('MessageSid')
        message_status = request.form.get('MessageStatus')
        
        logger.info(f"WhatsApp message {message_sid} status: {message_status}")
        
        return jsonify({"status": "ok"})

    def get_reports_api(self):
        """API endpoint untuk mendapatkan daftar laporan dari Supabase"""
        conn = self.db_manager.get_connection()
        if not conn:
            return jsonify({"error": "Supabase connection failed"}), 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT report_number, timestamp, caller_phone, location,
                           emergency_type, urgency_level, status, response_sent
                    FROM dev_moklet_ai.emergency_reports
                    ORDER BY timestamp DESC
                    LIMIT 50
                """)
                
                reports = cursor.fetchall()
                
                return jsonify({
                    "reports": [dict(report) for report in reports],
                    "total": len(reports)
                })
                
        except Exception as e:
            logger.error(f"Error getting reports from Supabase: {e}")
            return jsonify({"error": "Failed to fetch reports"}), 500
        finally:
            conn.close()

    def get_report_detail_api(self, report_id: str):
        """API endpoint untuk detail laporan dari Supabase"""
        conn = self.db_manager.get_connection()
        if not conn:
            return jsonify({"error": "Supabase connection failed"}), 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT * FROM dev_moklet_ai.emergency_reports
                    WHERE report_number = %s OR id::text = %s
                """, (report_id, report_id))
                
                report = cursor.fetchone()
                
                if not report:
                    return jsonify({"error": "Report not found"}), 404
                
                return jsonify({"report": dict(report)})
                
        except Exception as e:
            logger.error(f"Error getting report detail from Supabase: {e}")
            return jsonify({"error": "Failed to fetch report"}), 500
        finally:
            conn.close()

    def speak_response(self, text: str):
        """Speak response using TTS"""
        try:
            self.tts_engine.say(text)
            self.tts_engine.runAndWait()
        except Exception as e:
            logger.error(f"TTS error: {e}")

    def run_whatsapp_server(self, host='0.0.0.0', port=5000, debug=False):
        """Run Flask server untuk WhatsApp webhook"""
        logger.info(f"Starting WhatsApp webhook server on {host}:{port}")
        logger.info(f"Connected to Supabase database: {DB_CONFIG['host']}")
        self.flask_app.run(host=host, port=port, debug=debug)

    def run_voice_mode_enhanced(self):
        """Enhanced voice mode dengan better audio processing"""
        print("🔊 MODE SUARA ENHANCED AKTIF")
        print("Katakan 'berhenti' atau 'selesai' untuk mengakhiri")
        
        while True:
            try:
                # Enhanced audio listening
                audio_text = self.listen_to_audio_enhanced()
                if not audio_text:
                    continue
                
                # Cek perintah berhenti
                if any(word in audio_text.lower() for word in ['berhenti', 'stop', 'keluar', 'selesai', 'tutup']):
                    self.speak_response("Sistem suara dihentikan. Terima kasih.")
                    break
                
                # Process emergency report
                context = {
                    "source": "voice",
                    "input_method": "microphone"
                }
                
                extracted_info = self.extract_emergency_info_enhanced(audio_text, context)
                
                report_id = f"VC{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
                
                report = EmergencyReport(
                    id=report_id,
                    timestamp=datetime.datetime.now(),
                    caller_info="Voice Input - Local System",
                    caller_phone="N/A",
                    location=extracted_info.get('location', {}).get('raw_location', 'Tidak diketahui'),
                    emergency_type=EmergencyType(extracted_info.get('emergency_type', 'lainnya')),
                    urgency_level=UrgencyLevel(extracted_info.get('urgency_level', 3)),
                    description=audio_text,
                    structured_data=extracted_info,
                    ai_recommendations=extracted_info.get('immediate_actions', [])
                )
                
                # Save to Supabase database
                success = self.save_report_to_postgresql(report)
                
                # Generate dan berikan respons suara
                ai_response = self.generate_voice_response(extracted_info, audio_text, report_id)
                print(f"🤖 Respons: {ai_response}")
                self.speak_response(ai_response)
                
                # Tampilkan ringkasan enhanced
                self.display_report_summary(report, success)
                
            except KeyboardInterrupt:
                print("\n👋 Sistem suara dihentikan oleh pengguna")
                break
            except Exception as e:
                logger.error(f"Voice mode error: {e}")
                error_msg = "Terjadi kesalahan sistem. Silakan coba lagi."
                print(f"❌ Error: {error_msg}")
                self.speak_response(error_msg)

    def generate_voice_response(self, extracted_info: Dict, original_text: str, report_id: str) -> str:
        """Generate respons untuk voice interaction"""
        system_prompt = """
        Anda adalah operator darurat yang berpengalaman dan empatik untuk interaksi suara.
        Berikan respons yang:
        1. Menenangkan dan memberikan rasa aman
        2. Memberikan instruksi keselamatan yang jelas dan mudah diikuti
        3. Menggunakan bahasa yang natural untuk diucapkan
        4. Durasi bicara maksimal 30 detik
        5. Prioritaskan keselamatan pelapor
        """
        
        urgency = extracted_info.get('urgency_level', 3)
        emergency_type = extracted_info.get('emergency_type', 'lainnya')
        immediate_actions = extracted_info.get('immediate_actions', [])
        safety_instructions = extracted_info.get('safety_instructions', [])
        
        prompt = f"""
        Situasi darurat:
        - Jenis: {emergency_type}
        - Tingkat urgensi: {urgency}/5
        - Tindakan segera: {immediate_actions}
        - Instruksi keselamatan: {safety_instructions}
        - Nomor laporan: {report_id}
        
        Laporan asli: "{original_text}"
        
        Berikan respons suara yang tepat dan menenangkan.
        """
        
        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.4,
                max_tokens=200
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Tambahkan informasi laporan
            final_response = f"{ai_response} Nomor laporan Anda adalah {report_id}."
            
            return final_response
            
        except Exception as e:
            logger.error(f"Error generating voice response: {e}")
            return f"Terima kasih atas laporan Anda. Tim darurat akan segera menindaklanjuti. Nomor laporan Anda adalah {report_id}. Mohon tetap tenang dan pastikan keselamatan Anda."

    def display_report_summary(self, report: EmergencyReport, save_success: bool):
        """Display enhanced report summary"""
        print(f"\n{'='*60}")
        print(f"📋 RINGKASAN LAPORAN {report.id}")
        print(f"{'='*60}")
        print(f"🕐 Waktu: {report.timestamp.strftime('%d/%m/%Y %H:%M:%S')}")
        print(f"🏷️  Jenis: {report.emergency_type.value.upper().replace('_', ' ')}")
        print(f"⚠️  Urgensi: {report.urgency_level.value}/5 - {report.urgency_level.name}")
        print(f"📍 Lokasi: {report.location}")
        print(f"📱 Kontak: {report.caller_phone}")
        print(f"💾 Supabase: {'✅ Tersimpan' if save_success else '❌ Gagal'}")
        print(f"📊 Status: {report.status.value}")
        
        if report.structured_data.get('victims_info'):
            victims = report.structured_data['victims_info']
            if victims.get('count') and victims['count'] != 'tidak diketahui':
                print(f"👥 Korban: {victims['count']} - {victims.get('condition', 'kondisi tidak diketahui')}")
        
        if report.ai_recommendations:
            print(f"🎯 Rekomendasi AI:")
            for i, rec in enumerate(report.ai_recommendations[:3], 1):
                print(f"   {i}. {rec}")
        
        print(f"{'='*60}\n")

    def run_text_mode_enhanced(self):
        """Enhanced text mode dengan better processing"""
        print("💬 MODE TEKS ENHANCED AKTIF")
        print("Ketik 'exit', 'quit', atau 'keluar' untuk mengakhiri")
        
        while True:
            try:
                user_input = input("\n📞 Pelapor: ").strip()
                
                if user_input.lower() in ['exit', 'quit', 'keluar', 'selesai']:
                    print("👋 Terima kasih telah menggunakan sistem darurat")
                    break
                
                if not user_input:
                    print("❌ Mohon masukkan laporan darurat")
                    continue
                
                # Process emergency report dengan context
                context = {
                    "source": "text",
                    "input_method": "keyboard"
                }
                
                extracted_info = self.extract_emergency_info_enhanced(user_input, context)
                
                report_id = f"TXT{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
                
                report = EmergencyReport(
                    id=report_id,
                    timestamp=datetime.datetime.now(),
                    caller_info="Text Input - Local System",
                    caller_phone="N/A",
                    location=extracted_info.get('location', {}).get('raw_location', 'Tidak diketahui'),
                    emergency_type=EmergencyType(extracted_info.get('emergency_type', 'lainnya')),
                    urgency_level=UrgencyLevel(extracted_info.get('urgency_level', 3)),
                    description=user_input,
                    structured_data=extracted_info,
                    ai_recommendations=extracted_info.get('immediate_actions', [])
                )
                
                # Save to Supabase database
                success = self.save_report_to_postgresql(report)
                
                # Generate respons
                ai_response = self.generate_text_response(extracted_info, user_input, report_id)
                print(f"🤖 Operator: {ai_response}")
                
                # Display summary
                self.display_report_summary(report, success)
                
            except KeyboardInterrupt:
                print("\n👋 Sistem teks dihentikan")
                break
            except Exception as e:
                logger.error(f"Text mode error: {e}")
                print(f"❌ Error: {e}")

    def generate_text_response(self, extracted_info: Dict, original_text: str, report_id: str) -> str:
        """Generate respons untuk text interaction"""
        system_prompt = """
        Anda adalah operator darurat profesional untuk interaksi teks.
        Berikan respons yang:
        1. Profesional namun empati
        2. Memberikan instruksi yang jelas dan terstruktur  
        3. Menyertakan informasi yang relevan
        4. Panjang respons 2-4 kalimat
        5. Fokus pada tindakan konkret
        """
        
        urgency = extracted_info.get('urgency_level', 3)
        emergency_type = extracted_info.get('emergency_type', 'lainnya')
        location = extracted_info.get('location', {}).get('raw_location', 'lokasi Anda')
        immediate_actions = extracted_info.get('immediate_actions', [])
        
        prompt = f"""
        Informasi darurat:
        - Jenis: {emergency_type}
        - Urgensi: {urgency}/5
        - Lokasi: {location}
        - Tindakan segera: {immediate_actions}
        - Nomor laporan: {report_id}
        
        Laporan: "{original_text}"
        
        Berikan respons operator yang tepat.
        """
        
        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=250
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"Error generating text response: {e}")
            return f"Laporan darurat Anda telah diterima dengan nomor {report_id}. Tim emergency response akan segera menindaklanjuti. Mohon tetap tenang dan ikuti instruksi keselamatan yang sesuai."

    def get_dashboard_data_enhanced(self) -> Dict:
        """Enhanced dashboard data dengan Supabase PostgreSQL"""
        conn = self.db_manager.get_connection()
        if not conn:
            return {"error": "Supabase connection failed"}
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Total reports
                cursor.execute("SELECT COUNT(*) as total FROM dev_moklet_ai.emergency_reports")
                total_reports = cursor.fetchone()['total']
                
                # Emergency types distribution
                cursor.execute("""
                    SELECT emergency_type, COUNT(*) as count 
                    FROM dev_moklet_ai.emergency_reports 
                    GROUP BY emergency_type 
                    ORDER BY count DESC
                """)
                emergency_types = {row['emergency_type']: row['count'] for row in cursor.fetchall()}
                
                # Urgency distribution
                cursor.execute("""
                    SELECT urgency_level, COUNT(*) as count 
                    FROM dev_moklet_ai.emergency_reports 
                    GROUP BY urgency_level 
                    ORDER BY urgency_level
                """)
                urgency_distribution = {row['urgency_level']: row['count'] for row in cursor.fetchall()}
                
                # Location hotspots
                cursor.execute("""
                    SELECT location, COUNT(*) as count 
                    FROM dev_moklet_ai.emergency_reports 
                    WHERE location != 'Tidak diketahui' AND location != 'tidak spesifik'
                    GROUP BY location 
                    ORDER BY count DESC 
                    LIMIT 10
                """)
                location_hotspots = {row['location']: row['count'] for row in cursor.fetchall()}
                
                # Recent reports
                cursor.execute("""
                    SELECT report_number, timestamp, emergency_type, urgency_level, 
                           location, status, caller_phone
                    FROM dev_moklet_ai.emergency_reports 
                    ORDER BY timestamp DESC 
                    LIMIT 10
                """)
                recent_reports = [dict(row) for row in cursor.fetchall()]
                
                # Response rate
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN response_sent THEN 1 ELSE 0 END) as responded
                    FROM dev_moklet_ai.emergency_reports
                """)
                response_stats = cursor.fetchone()
                response_rate = (response_stats['responded'] / response_stats['total'] * 100) if response_stats['total'] > 0 else 0
                
                # Status distribution
                cursor.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM dev_moklet_ai.emergency_reports 
                    GROUP BY status
                """)
                status_distribution = {row['status']: row['count'] for row in cursor.fetchall()}
                
                return {
                    "total_reports": total_reports,
                    "emergency_types": emergency_types,
                    "urgency_distribution": urgency_distribution,
                    "location_hotspots": location_hotspots,
                    "recent_reports": recent_reports,
                    "response_rate": round(response_rate, 2),
                    "status_distribution": status_distribution,
                    "database": "Supabase PostgreSQL",
                    "whatsapp_integration": "Active",
                    "voice_processing": "Enhanced"
                }
                
        except Exception as e:
            logger.error(f"Error getting dashboard data from Supabase: {e}")
            return {"error": f"Failed to fetch dashboard data: {e}"}
        finally:
            conn.close()

    def show_dashboard_enhanced(self):
        """Enhanced dashboard display dengan Supabase info"""
        data = self.get_dashboard_data_enhanced()
        
        if "error" in data:
            print(f"❌ Error: {data['error']}")
            return
        
        print("\n" + "="*80)
        print("🏢 SISTEM DARURAT NLP - DASHBOARD ANALYTICS ENHANCED (SUPABASE)")
        print("="*80)
        
        print(f"\n📊 STATISTIK UMUM")
        print(f"├── Total Laporan: {data['total_reports']}")
        print(f"├── Response Rate: {data['response_rate']}%")
        print(f"├── Database: {data['database']}")
        print(f"├── WhatsApp Integration: {data['whatsapp_integration']}")
        print(f"└── Voice Processing: {data['voice_processing']}")
        
        print(f"\n🔥 JENIS DARURAT (Top 5)")
        for i, (em_type, count) in enumerate(list(data['emergency_types'].items())[:5], 1):
            print(f"├── {i}. {em_type.replace('_', ' ').title()}: {count} laporan")
        
        print(f"\n⚠️  DISTRIBUSI URGENSI")
        urgency_labels = {1: "Informasi", 2: "Rendah", 3: "Sedang", 4: "Tinggi", 5: "KRITIS"}
        for level in sorted(data['urgency_distribution'].keys()):
            count = data['urgency_distribution'][level]
            label = urgency_labels.get(level, f"Level {level}")
            print(f"├── {label}: {count} laporan")
        
        print(f"\n📊 STATUS LAPORAN")
        for status, count in data['status_distribution'].items():
            print(f"├── {status}: {count} laporan")
        
        print(f"\n🗺️  HOTSPOT LOKASI (Top 5)")
        for i, (location, count) in enumerate(list(data['location_hotspots'].items())[:5], 1):
            print(f"├── {i}. {location}: {count} laporan")
        
        print(f"\n📱 LAPORAN TERBARU (5 Terakhir)")
        for i, report in enumerate(data['recent_reports'][:5], 1):
            timestamp = datetime.datetime.fromisoformat(report['timestamp'].replace('Z', '+00:00'))
            print(f"├── {i}. [{report['report_number']}] {report['emergency_type'].title()}")
            print(f"│   └── {timestamp.strftime('%d/%m/%Y %H:%M')} | Urgensi: {report['urgency_level']}/5 | {report['status']}")
        
        print("\n" + "="*80)
        
        # Performance metrics
        print(f"\n🚀 METRICS PERFORMA")
        print(f"├── Database: Supabase PostgreSQL ✅")
        print(f"├── Connection: {DB_CONFIG['host']} ✅")
        print(f"├── AI Processing: GPT-4 ✅") 
        print(f"├── Voice Recognition: Multi-engine ✅")
        print(f"└── WhatsApp Bot: Twilio API ✅")
        
        print("\n" + "="*80)

def create_startup_script_supabase():
    """Create startup script untuk deployment dengan Supabase"""
    startup_script = """#!/bin/bash
# Emergency NLP System Startup Script - Supabase Edition

echo "🚨 Starting Emergency NLP System with Supabase..."

# Install dependencies
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

# Setup environment variables
export FLASK_APP=emergency_nlp_system.py
export FLASK_ENV=production

# Load environment variables dari .env file
if [ -f .env ]; then
    echo "📋 Loading environment variables from .env..."
    export $(cat .env | xargs)
fi

# Test Supabase connection
echo "🔗 Testing Supabase connection..."
python -c "
from emergency_nlp_system import SupabasePostgreSQLManager
manager = SupabasePostgreSQLManager()
conn = manager.get_connection()
if conn:
    print('✅ Supabase connection successful')
    conn.close()
else:
    print('❌ Supabase connection failed')
    exit(1)
"

# Start the application
echo "🔧 Starting Flask webhook server..."
python emergency_nlp_system.py --mode server --port 5000 &

echo "✅ Emergency NLP System started successfully!"
echo "📱 WhatsApp webhook: http://your-domain:5000/webhook/whatsapp"
echo "📊 API Reports: http://your-domain:5000/api/reports"
echo "🏥 Health Check: http://your-domain:5000/health"
echo "🗄️  Database: Supabase PostgreSQL"
"""
    
    with open("startup_script.sh", "w", encoding="utf-8") as f:
        f.write(startup_script)
    
    os.chmod("start_system_supabase.sh", 0o755)
    print("📄 Supabase startup script created: start_system_supabase.sh")

def create_requirements_file_supabase():
    """Create requirements.txt file untuk Supabase"""
    requirements = """openai>=1.0.0
speechrecognition>=3.10.0
pyttsx3>=2.90
pandas>=1.5.0
psycopg2-binary>=2.9.0
flask>=2.0.0
twilio>=8.0.0
requests>=2.28.0
pyaudio>=0.2.11
pydub>=0.25.1
python-dotenv>=0.19.0
gunicorn>=20.1.0
urllib3>=1.26.0
"""
    
    with open("requirements_supabase.txt", "w") as f:
        f.write(requirements)
    
    print("📄 Supabase requirements file created: requirements_supabase.txt")

def create_env_template_supabase():
    """Create .env template file untuk Supabase"""
    env_template = """# OpenAI Configuration
OPENAI_API_KEY=

# Twilio WhatsApp Configuration
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886

# Supabase PostgreSQL Configuration
DATABASE_URL=
DIRECT_URL=

# Alternative individual DB config (used as fallback)
DB_HOST=
DB_PORT=
DB_NAME=
DB_USER=
DB_PASSWORD=

# Authentication
AUTH_SECRET=

# Flask Configuration
FLASK_SECRET_KEY=
FLASK_DEBUG=

# System Configuration
VOICE_RECOGNITION_TIMEOUT=
MAX_AUDIO_FILE_SIZE=
"""
    
    with open(".env.supabase.template", "w") as f:
        f.write(env_template)
    
    print("📄 Supabase environment template created: .env.supabase.template")

def main_enhanced():
    """Enhanced main function dengan mode selection dan Supabase support"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Emergency NLP System Enhanced - Supabase Edition')
    parser.add_argument('--mode', choices=['text', 'voice', 'server', 'dashboard'], 
                       default='menu', help='Run mode')
    parser.add_argument('--port', type=int, default=5000, help='Server port')
    parser.add_argument('--host', default='0.0.0.0', help='Server host')
    
    args = parser.parse_args()
    
    # Create necessary files untuk Supabase
    create_requirements_file_supabase()
    create_env_template_supabase()
    create_startup_script_supabase()
    
    # Initialize system
    system = EnhancedEmergencyNLPSystem()
    
    if args.mode == 'server':
        # Run WhatsApp webhook server
        print("🚀 Starting WhatsApp webhook server with Supabase...")
        system.run_whatsapp_server(host=args.host, port=args.port)
        
    elif args.mode == 'text':
        system.run_text_mode_enhanced()
        
    elif args.mode == 'voice':
        system.run_voice_mode_enhanced()
        
    elif args.mode == 'dashboard':
        system.show_dashboard_enhanced()
        
    else:
        # Interactive menu
        print("🚨 SISTEM NLP DARURAT - SUPABASE ENHANCED VERSION")
        print("=" * 50)
        print("1. Mode Teks Enhanced")
        print("2. Mode Suara Enhanced") 
        print("3. Dashboard Analytics Enhanced")
        print("4. Start WhatsApp Server")
        print("5. Test Supabase Connection")
        print("6. Keluar")
        
        while True:
            try:
                choice = input("\nPilih mode (1-6): ").strip()
                
                if choice == "1":
                    system.run_text_mode_enhanced()
                elif choice == "2":
                    system.run_voice_mode_enhanced()
                elif choice == "3":
                    system.show_dashboard_enhanced()
                elif choice == "4":
                    port = input("Port (default 5000): ").strip() or "5000"
                    system.run_whatsapp_server(port=int(port))
                elif choice == "5":
                    # Test Supabase connection
                    conn = system.db_manager.get_connection()
                    if conn:
                        print("✅ Supabase connection successful!")
                        conn.close()
                    else:
                        print("❌ Supabase connection failed!")
                elif choice == "6":
                    print("👋 Sistem ditutup")
                    break
                else:
                    print("❌ Pilihan tidak valid")
                    
            except KeyboardInterrupt:
                print("\n👋 Sistem ditutup")
                break
            except Exception as e:
                logger.error(f"Main error: {e}")
                print(f"❌ Error: {e}")

app = Flask(__name__)
CORS(app)
if __name__ == "__main__":
    print("=" * 60)
    print("🚨 EMERGENCY NLP SYSTEM - SUPABASE ENHANCED VERSION")
    print("=" * 60)
    print("📦 Features:")
    print("✅ Supabase PostgreSQL Integration")
    print("✅ WhatsApp Bot Integration")
    print("✅ Enhanced Voice Recognition") 
    print("✅ Multi-engine Speech Processing")
    print("✅ Real-time Analytics Dashboard")
    print("✅ RESTful API Endpoints")
    print("✅ Cloud Database Support")
    print("=" * 60)
    
    main_enhanced()