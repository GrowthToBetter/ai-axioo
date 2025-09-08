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
import os, sys
import logging
from flask import Flask, request, jsonify, make_response
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
# TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
# TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
# TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
# twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN")
FACEBOOK_PHONE_NUMBER_ID = os.getenv("FACEBOOK_PHONE_NUMBER_ID")
FACEBOOK_WEBHOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_WEBHOOK_VERIFY_TOKEN")

class FacebookWhatsAppManager:
    def __init__(self):
        self.access_token = FACEBOOK_ACCESS_TOKEN
        self.phone_number_id = FACEBOOK_PHONE_NUMBER_ID
        self.base_url = f"https://graph.facebook.com/v17.0/{self.phone_number_id}/messages"
        self.headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
    
    def send_message(self, to_number: str, message: str) -> bool:
        """Send WhatsApp message via Facebook API"""
        try:
            # Clean phone number (remove whatsapp: prefix if present)
            clean_number = to_number.replace("whatsapp:", "").strip()
            
            payload = {
                "messaging_product": "whatsapp",
                "to": clean_number,
                "type": "text",
                "text": {
                    "body": message
                }
            }
            
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                logger.info(f"Facebook WhatsApp message sent to {clean_number}")
                return True
            else:
                logger.error(f"Facebook API error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending Facebook WhatsApp message: {e}")
            return False

# Ganti inisialisasi Twilio dengan Facebook
facebook_whatsapp = FacebookWhatsAppManager()

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
    def __init__(self, use_microphone=False):
        self.recognizer = sr.Recognizer()
        self.microphone = None
        if use_microphone:
            try:
                self.microphone = sr.Microphone()
                print("✅ Microphone initialized (local mode)")
            except Exception as e:
                print(f"⚠️  Microphone not available: {e}")
                self.microphone = None
        else:
            print("🔇 Microphone disabled (running on server)")
        self.tts_engine = pyttsx3.init()
        self.db_manager = SupabasePostgreSQLManager()
        self.audio_queue = queue.Queue()
        
        # Initialize Flask app with CORS support
        self.flask_app = Flask(__name__)
        CORS(self.flask_app)  # Enable CORS for all routes
        
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
        # Enable CORS for all routes
        CORS(self.flask_app, 
             origins=["*"],  # Allow all origins for development
             methods=["GET", "POST", "OPTIONS"],
             allow_headers=["Content-Type", "Authorization"])

        # Facebook webhook verification (GET)
        @self.flask_app.route('/webhook/whatsapp', methods=['GET'])
        def verify_webhook():
            """Verify Facebook webhook"""
            mode = request.args.get('hub.mode')
            token = request.args.get('hub.verify_token')
            challenge = request.args.get('hub.challenge')

            if mode == 'subscribe' and token == FACEBOOK_WEBHOOK_VERIFY_TOKEN:
                logger.info("Facebook webhook verified successfully")
                return challenge, 200
            else:
                logger.error("Facebook webhook verification failed")
                return "Verification failed", 403

        # Facebook webhook for incoming messages (POST)
        @self.flask_app.route('/webhook/whatsapp', methods=['POST'])
        def facebook_webhook():
            return self.handle_facebook_webhook()

        # Health check endpoint - FIXED
        @self.flask_app.route('/health', methods=['GET'])
        def health_check():
            """Health check endpoint for Render"""
            try:
                # Test database connection
                conn = self.db_manager.get_connection()
                db_status = "connected" if conn else "disconnected"
                if conn:
                    conn.close()
                
                # Test OpenAI API
                openai_status = "ok" if openai.api_key else "missing_key"
                
                return jsonify({
                    "status": "healthy",
                    "timestamp": datetime.datetime.now().isoformat(),
                    "database": db_status,
                    "openai": openai_status,
                    "environment": os.getenv("FLASK_ENV", "development"),
                    "version": "1.0.0",
                    "facebook_token_set": bool(FACEBOOK_ACCESS_TOKEN),
                    "webhook_token_set": bool(FACEBOOK_WEBHOOK_VERIFY_TOKEN)
                }), 200
                
            except Exception as e:
                logger.error(f"Health check error: {e}")
                return jsonify({
                    "status": "unhealthy", 
                    "error": str(e),
                    "timestamp": datetime.datetime.now().isoformat()
                }), 500
        
        # Root endpoint - FIXED
        @self.flask_app.route('/', methods=['GET'])
        def index():
            """Root endpoint"""
            return jsonify({
                "service": "Emergency NLP System",
                "status": "running",
                "endpoints": {
                    "webhook": "/webhook/whatsapp",
                    "json_api": "/webhook/whatsapp/json",
                    "health": "/health",
                    "reports": "/api/reports"
                },
                "facebook_integration": "active" if FACEBOOK_ACCESS_TOKEN else "inactive"
            })
        
        # Reports API endpoint - FIXED
        @self.flask_app.route('/api/reports', methods=['GET'])
        def get_reports():
            """API endpoint for reports"""
            try:
                return self.get_reports_api()
            except Exception as e:
                logger.error(f"Reports API error: {e}")
                return jsonify({"error": str(e)}), 500
        
        # Add error handlers - FIXED
        @self.flask_app.errorhandler(404)
        def not_found(error):
            logger.warning(f"404 error: {request.url}")
            return jsonify({
                "error": "Endpoint not found", 
                "requested_path": request.path,
                "available_endpoints": ["/", "/health", "/webhook/whatsapp", "/api/reports"]
            }), 404
        
        @self.flask_app.errorhandler(500)
        def internal_error(error):
            logger.error(f"500 error: {error}")
            return jsonify({"error": "Internal server error"}), 500

        # JSON-only endpoint for web clients (unchanged)
        @self.flask_app.route("/webhook/whatsapp/json", methods=["POST", "OPTIONS"])
        def handle_whatsapp_message_json():
            """JSON-only endpoint for web clients (Next.js) - Does NOT send WhatsApp messages"""
            if request.method == "OPTIONS":
                response = make_response()
                response.headers.add("Access-Control-Allow-Origin", "*")
                response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
                response.headers.add("Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS")
                return response

            try:
                # Parse JSON body
                data = request.get_json()
                if not data:
                    logger.error("No JSON data received")
                    return jsonify({
                        "status": "error",
                        "message": "Invalid JSON or missing data"
                    }), 400

                # Extract fields
                from_number = data.get("From", "").replace("whatsapp:", "").strip()
                message_body = data.get("Body", "").strip()

                # Validate
                if not from_number:
                    return jsonify({
                        "status": "error",
                        "message": "Missing sender phone number"
                    }), 400

                if not message_body:
                    return jsonify({
                        "status": "error",
                        "message": "Pesan tidak boleh kosong"
                    }), 400

                logger.info(f"Web chat message from {from_number}: {message_body[:100]}...")

                # Context for AI
                context = {
                    "source": "web",
                    "phone_number": from_number,
                    "message_type": "text",
                    "timestamp": datetime.datetime.now().isoformat()
                }

                # Step 1: Extract emergency info using GPT-4
                extracted_info = self.extract_emergency_info_enhanced(message_body, context)

                # Safely extract and validate urgency_level
                try:
                    urgency_level = int(extracted_info.get("urgency_level", 3))
                    if urgency_level < 1:
                        urgency_level = 1
                    elif urgency_level > 5:
                        urgency_level = 5
                except (TypeError, ValueError):
                    urgency_level = 3  # fallback

                # Safely extract and validate emergency_type
                raw_emergency_type = str(extracted_info.get("emergency_type", "lainnya")).lower().strip()
                valid_emergency_types = {e.value for e in EmergencyType}

                if raw_emergency_type not in valid_emergency_types:
                    logger.warning(f"Invalid emergency_type '{raw_emergency_type}', falling back to 'lainnya'")
                    raw_emergency_type = "lainnya"

                emergency_type = EmergencyType(raw_emergency_type)
                raw_location = extracted_info.get("location", {}).get("raw_location", "tidak diketahui")

                # Generate report ID
                report_id = f"WEB{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

                # Create EmergencyReport
                report = EmergencyReport(
                    id=report_id,
                    timestamp=datetime.datetime.now(),
                    caller_info=f"Web Chat: {from_number}",
                    caller_phone=from_number,
                    location=raw_location,
                    emergency_type=emergency_type,
                    urgency_level=UrgencyLevel(urgency_level),
                    description=message_body,
                    structured_data=extracted_info,
                    ai_recommendations=extracted_info.get("immediate_actions", []),
                    status=ReportStatus.BARU
                )

                # Save to database
                db_success = self.save_report_to_postgresql(report)
                self.save_whatsapp_conversation(
                    from_number, message_body, f"SID_{report_id}", "text", None, report_id
                )

                # Step 2: Generate clean, professional emergency response via GPT-4
                system_prompt = """
                Anda adalah operator darurat profesional di Indonesia. Tugas Anda adalah memberikan respons yang:
                - Profesional, empatik, dan jelas
                - Terstruktur dalam langkah-langkah mudah diikuti
                - Fokus pada keselamatan pengguna
                - Tidak terlalu panjang (maks 200 kata)
                - Cocok ditampilkan di antarmuka web chat
                Gunakan format:
                1. Konfirmasi laporan
                2. Langkah keselamatan segera
                3. Informasi tambahan (kontak darurat, nomor laporan)
                4. Reassurance (tim sedang menindaklanjuti)
                """

                user_prompt = f"""
                Laporan dari pengguna:
                "{message_body}"

                Informasi ekstraksi:
                - Jenis Darurat: {raw_emergency_type}
                - Lokasi: {raw_location}
                - Tingkat Urgensi: {urgency_level}/5
                - Tindakan segera: {', '.join(extracted_info.get('immediate_actions', []))}

                Nomor Laporan: {report_id}

                Buat respons profesional untuk ditampilkan di web chat.
                """

                try:
                    response = client.chat.completions.create(
                        model="gpt-4",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.3,
                        max_tokens=300
                    )
                    ai_response = response.choices[0].message.content.strip()
                except Exception as openai_error:
                    logger.error(f"OpenAI generation error: {openai_error}")
                    ai_response = (
                        f"Laporan darurat Anda (No: {report_id}) telah diterima. "
                        "Tim darurat sedang meninjau situasi. "
                        "Langkah segera: pastikan keselamatan Anda, jauhi bahaya, dan tunggu bantuan. "
                        "📞 Hubungi 112 jika situasi kritis."
                    )

                # Final response
                response_data = {
                    "status": "success",
                    "message": ai_response,
                    "report_id": report_id,
                    "emergency_type": raw_emergency_type,
                    "urgency_level": urgency_level,
                    "location": raw_location,
                    "database_saved": db_success,
                    "emergency_info": extracted_info,
                    "timestamp": datetime.datetime.now().isoformat(),
                    "contact": {
                        "emergency_number": "112",
                        "report_followup": f"Gunakan nomor laporan: {report_id}"
                    }
                }

                logger.info(f"Web chat response generated for {report_id}")

                # Add CORS headers
                response = jsonify(response_data)
                response.headers.add("Access-Control-Allow-Origin", "*")
                return response, 200

            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
                return jsonify({"status": "error", "message": "Format JSON tidak valid"}), 400
            except Exception as e:
                logger.error(f"Unexpected error in /webhook/whatsapp/json: {e}", exc_info=True)
                return jsonify({
                    "status": "error",
                    "message": "Terjadi kesalahan internal sistem",
                    "timestamp": datetime.datetime.now().isoformat()
                }), 500
    
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

    # def send_whatsapp_message(self, to_number: str, message: str) -> bool:
    #     """Send WhatsApp message via Twilio - Only called for regular webhook requests"""
    #     try:
    #         logger.info(f"Sending WhatsApp message to {to_number}")
    #         message = twilio_client.messages.create(
    #             body=message,
    #             from_=TWILIO_WHATSAPP_NUMBER,
    #             to=f"whatsapp:{to_number}"
    #         )
            
    #         logger.info(f"WhatsApp message sent to {to_number}: {message.sid}")
    #         return True
            
    #     except Exception as e:
    #         logger.error(f"Error sending WhatsApp message: {e}")
    #         return False
    def send_whatsapp_message(self, to_number: str, message: str) -> bool:
        """Send WhatsApp message via Facebook API - Only called for regular webhook requests"""
        return facebook_whatsapp.send_message(to_number, message)
    
    # def handle_whatsapp_message(self):
    #     """Handle incoming WhatsApp messages from Twilio webhook - SENDS WhatsApp messages"""
    #     # Initialize response data structure
    #     response_data = {
    #         "status": "success",
    #         "message": "",
    #         "report_id": None,
    #         "emergency_info": {},
    #         "error": None
    #     }

    #     try:
    #         # Get message data from Twilio webhook
    #         message_sid = request.form.get('MessageSid')
    #         from_number = request.form.get('From', '').replace('whatsapp:', '')
    #         message_body = request.form.get('Body', '')
    #         media_url = request.form.get('MediaUrl0')
    #         media_content_type = request.form.get('MediaContentType0', '')

    #         logger.info(f"WhatsApp webhook from {from_number}: {message_body[:100]}")

    #         # Process voice message with improved handling
    #         if media_url and 'audio' in media_content_type:
    #             logger.info("Processing voice message...")

    #             try:
    #                 # Download voice file with proper authentication
    #                 auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    #                 response = requests.get(media_url, auth=auth, timeout=30)

    #                 if response.status_code == 200:
    #                     # Create temporary files for processing
    #                     with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
    #                         ogg_file.write(response.content)
    #                         ogg_path = ogg_file.name

    #                     # Convert OGG to WAV using OpenAI Whisper API directly
    #                     try:
    #                         # Use OpenAI Whisper API which can handle OGG files directly
    #                         with open(ogg_path, 'rb') as audio_file:
    #                             transcription = client.audio.transcriptions.create(
    #                                 model="whisper-1",
    #                                 file=audio_file,
    #                                 language="id"  # Indonesian language
    #                             )

    #                         transcribed_text = transcription.text

    #                         if transcribed_text and transcribed_text.strip():
    #                             message_body = transcribed_text
    #                             message_type = "voice"
    #                             logger.info(f"Voice message transcribed successfully: {transcribed_text[:100]}...")
    #                             response_data["transcribed_text"] = transcribed_text
    #                         else:
    #                             logger.warning("Voice transcription returned empty text")
    #                             response_msg = "Maaf, tidak dapat memproses pesan suara. Silakan kirim pesan teks atau coba lagi."
    #                             self.send_whatsapp_message(from_number, response_msg)  # SEND to WhatsApp
    #                             response_data["status"] = "error"
    #                             response_data["error"] = "Empty voice transcription"
    #                             response_data["message"] = response_msg
    #                             return str(MessagingResponse())

    #                     except Exception as whisper_error:
    #                         logger.error(f"Whisper transcription error: {whisper_error}")
    #                         response_msg = "Maaf, tidak dapat memproses pesan suara. Silakan kirim pesan teks atau coba lagi."
    #                         self.send_whatsapp_message(from_number, response_msg)  # SEND to WhatsApp
    #                         response_data["status"] = "error"
    #                         response_data["error"] = str(whisper_error)
    #                         response_data["message"] = response_msg
    #                         return str(MessagingResponse())

    #                     finally:
    #                         # Clean up temporary file
    #                         if os.path.exists(ogg_path):
    #                             try:
    #                                 os.unlink(ogg_path)
    #                             except Exception as e:
    #                                 logger.warning(f"Failed to delete temp file {ogg_path}: {e}")

    #                 else:
    #                     logger.error(f"Failed to download voice message: HTTP {response.status_code}")
    #                     response_msg = "Maaf, gagal mengunduh pesan suara. Silakan coba kirim ulang atau gunakan pesan teks."
    #                     self.send_whatsapp_message(from_number, response_msg)  # SEND to WhatsApp
    #                     response_data["status"] = "error"
    #                     response_data["error"] = f"Failed to download voice message: HTTP {response.status_code}"
    #                     response_data["message"] = response_msg
    #                     return str(MessagingResponse())

    #             except requests.exceptions.RequestException as e:
    #                 logger.error(f"Error downloading voice message: {e}")
    #                 response_msg = "Maaf, terjadi kesalahan saat memproses pesan suara. Silakan kirim pesan teks."
    #                 self.send_whatsapp_message(from_number, response_msg)  # SEND to WhatsApp
    #                 response_data["status"] = "error"
    #                 response_data["error"] = str(e)
    #                 response_data["message"] = response_msg
    #                 return str(MessagingResponse())

    #         else:
    #             message_type = "text"

    #         # Process emergency report only if we have message content
    #         if message_body and message_body.strip():
    #             context = {
    #                 "source": "whatsapp",
    #                 "phone_number": from_number,
    #                 "message_type": message_type
    #             }

    #             # Create emergency report
    #             extracted_info = self.extract_emergency_info_enhanced(message_body, context)

    #             report_id = f"WA{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

    #             report = EmergencyReport(
    #                 id=report_id,
    #                 timestamp=datetime.datetime.now(),
    #                 caller_info=f"WhatsApp: {from_number}",
    #                 caller_phone=from_number,
    #                 location=extracted_info.get('location', {}).get('raw_location', 'Tidak diketahui'),
    #                 emergency_type=EmergencyType(extracted_info.get('emergency_type', 'lainnya')),
    #                 urgency_level=UrgencyLevel(extracted_info.get('urgency_level', 3)),
    #                 description=message_body,
    #                 structured_data=extracted_info,
    #                 ai_recommendations=extracted_info.get('immediate_actions', []),
    #                 voice_file_path=media_url if message_type == "voice" else None
    #             )

    #             # Save to database
    #             success = self.save_report_to_postgresql(report)
    #             self.save_whatsapp_conversation(
    #                 from_number, message_body, message_sid, 
    #                 message_type, media_url, report_id
    #             )

    #             # Generate comprehensive step-by-step response
    #             response_text = self.generate_comprehensive_emergency_response(
    #                 extracted_info, message_body, report_id, from_number
    #             )

    #             # Send response via WhatsApp (for regular webhook endpoint)
    #             self.send_comprehensive_response_parts(from_number, response_text, report_id)

    #             # Update response sent status
    #             if success:
    #                 self.update_report_response_status(report_id, True)

    #             # Populate response data
    #             response_data["status"] = "success"
    #             response_data["message"] = "Response sent via WhatsApp"
    #             response_data["report_id"] = report_id
    #             response_data["emergency_info"] = {
    #                 "emergency_type": extracted_info.get('emergency_type', 'lainnya'),
    #                 "urgency_level": extracted_info.get('urgency_level', 3),
    #                 "location": extracted_info.get('location', {}).get('raw_location', 'Tidak diketahui'),
    #                 "immediate_actions": extracted_info.get('immediate_actions', []),
    #                 "caller_phone": from_number,
    #                 "message_type": message_type
    #             }

    #         else:
    #             # Handle empty message case
    #             logger.warning(f"Empty message received from {from_number}")
    #             response_msg = "Maaf, pesan kosong diterima. Silakan kirim laporan darurat Anda dengan detail yang jelas."
    #             self.send_whatsapp_message(from_number, response_msg)  # SEND to WhatsApp
    #             response_data["status"] = "error"
    #             response_data["error"] = "Empty message received"
    #             response_data["message"] = response_msg

    #         return str(MessagingResponse())

    #     except Exception as e:
    #         logger.error(f"Error handling WhatsApp message: {e}")
    #         error_response = "Terjadi kesalahan sistem. Tim teknis akan segera menindaklanjuti laporan Anda."
    #         try:
    #             # Try to get phone number for error response
    #             from_number = request.form.get('From', '').replace('whatsapp:', '')
    #             if from_number:
    #                 self.send_whatsapp_message(from_number, error_response)  # SEND to WhatsApp
    #         except Exception as send_error:
    #             logger.error(f"Failed to send error response: {send_error}")

    #         response_data["status"] = "error"
    #         response_data["error"] = str(e)
    #         response_data["message"] = error_response

    #         return str(MessagingResponse().message(error_response))
    def handle_facebook_webhook(self):
        """Optimized Facebook webhook handler"""
        try:
            data = request.get_json()
            
            if not data:
                return jsonify({"status": "ok"}), 200
            
            # Process messages efficiently
            if 'entry' in data:
                for entry in data['entry']:
                    if 'changes' in entry:
                        for change in entry['changes']:
                            if change.get('field') == 'messages':
                                value = change.get('value', {})
                                
                                if 'messages' in value:
                                    for message in value['messages']:
                                        from_number = message.get('from')
                                        message_id = message.get('id')
                                        
                                        # Handle text message
                                        if message.get('type') == 'text':
                                            message_body = message.get('text', {}).get('body', '')
                                            # Process in background to avoid blocking webhook
                                            threading.Thread(
                                                target=self.process_facebook_message,
                                                args=(from_number, message_body, message_id, 'text')
                                            ).start()
                                        
                                        # Handle voice message
                                        elif message.get('type') == 'audio':
                                            audio_data = message.get('audio', {})
                                            media_id = audio_data.get('id')
                                            media_url = self.get_facebook_media_url(media_id)
                                            threading.Thread(
                                                target=self.process_facebook_message,
                                                args=(from_number, '', message_id, 'voice', media_url)
                                            ).start()
            
            return jsonify({"status": "ok"}), 200
            
        except Exception as e:
            logger.error(f"Error handling Facebook webhook: {e}")
            return jsonify({"status": "error"}), 500
    def get_facebook_media_url(self, media_id: str) -> str:
        """Get media URL from Facebook API"""
        try:
            url = f"https://graph.facebook.com/v17.0/{media_id}"
            headers = {'Authorization': f'Bearer {FACEBOOK_ACCESS_TOKEN}'}
            
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                return response.json().get('url', '')
            
        except Exception as e:
            logger.error(f"Error getting Facebook media URL: {e}")
        
        return ''
    
    def process_facebook_message(self, from_number: str, message_body: str, 
                               message_id: str, message_type: str, media_url: str = None):
        """Optimized Facebook WhatsApp message processing - SINGLE RESPONSE"""
        try:
            # Handle voice message
            if message_type == 'voice' and media_url:
                transcribed_text = self.process_facebook_voice(media_url)
                if transcribed_text:
                    message_body = transcribed_text
                else:
                    self.send_whatsapp_message(
                        from_number, 
                        "Maaf, tidak dapat memproses pesan suara. Silakan kirim pesan teks."
                    )
                    return
            
            if not message_body.strip():
                self.send_whatsapp_message(
                    from_number,
                    "Maaf, pesan kosong diterima. Silakan kirim laporan darurat Anda."
                )
                return
            
            # Single processing and response generation
            context = {
                "source": "facebook_whatsapp",
                "phone_number": from_number,
                "message_type": message_type
            }
            
            # Extract info and create report in one flow
            extracted_info = self.extract_emergency_info_enhanced(message_body, context)
            report_id = f"FB{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            # Create report
            report = EmergencyReport(
                id=report_id,
                timestamp=datetime.datetime.now(),
                caller_info=f"Facebook WhatsApp: {from_number}",
                caller_phone=from_number,
                location=extracted_info.get('location', {}).get('raw_location', 'Tidak diketahui'),
                emergency_type=EmergencyType(extracted_info.get('emergency_type', 'lainnya')),
                urgency_level=UrgencyLevel(extracted_info.get('urgency_level', 3)),
                description=message_body,
                structured_data=extracted_info,
                ai_recommendations=extracted_info.get('immediate_actions', []),
                voice_file_path=media_url if message_type == "voice" else None
            )
            
            # Generate SINGLE optimized response
            response_text = self.generate_optimized_emergency_response(
                extracted_info, message_body, report_id, from_number
            )
            
            # Save to database (async to not block response)
            try:
                success = self.save_report_to_postgresql(report)
                self.save_whatsapp_conversation(
                    from_number, message_body, message_id, 
                    message_type, media_url, report_id
                )
                if success:
                    self.update_report_response_status(report_id, True)
            except Exception as db_error:
                logger.error(f"Database save error: {db_error}")
                # Don't let DB errors block emergency response
            
            # Send SINGLE response
            self.send_whatsapp_message(from_number, response_text)
            logger.info(f"Single optimized response sent to {from_number} for report {report_id}")
            
        except Exception as e:
            logger.error(f"Error processing Facebook message: {e}")
            # Simple error response
            self.send_whatsapp_message(
                from_number,
                "🚨 Laporan diterima. Sistem sedang memproses. Tim teknis akan segera menindaklanjuti."
            )
    def process_facebook_voice(self, media_url: str) -> str:
        """Process voice message from Facebook"""
        try:
            # Download voice file with Facebook authentication
            headers = {'Authorization': f'Bearer {FACEBOOK_ACCESS_TOKEN}'}
            response = requests.get(media_url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
                    tmp_file.write(response.content)
                    temp_path = tmp_file.name
                
                # Use OpenAI Whisper for transcription
                try:
                    with open(temp_path, 'rb') as audio_file:
                        transcription = client.audio.transcriptions.create(
                            model="whisper-1",
                            file=audio_file,
                            language="id"
                        )
                    
                    return transcription.text
                    
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
            
        except Exception as e:
            logger.error(f"Error processing Facebook voice: {e}")
        
        return None
        
    def generate_optimized_emergency_response(self, extracted_info: Dict, original_text: str, report_id: str, phone_number: str) -> str:
        """Generate single optimized emergency response for WhatsApp"""
        
        system_prompt = """
        Anda adalah operator darurat profesional Indonesia. Berikan respons WhatsApp yang:
        
        1. SATU PESAN LENGKAP (maksimal 500 karakter untuk WhatsApp)
        2. Struktur terorganisir dengan emoji sebagai separator
        3. Informasi prioritas: konfirmasi → tindakan segera → kontak darurat → nomor laporan
        4. Bahasa Indonesia yang jelas dan menenangkan
        5. Actionable dan tidak membuang waktu
        
        Format respons:
        🚨 [Konfirmasi singkat situasi]
        
        ⚡ SEGERA:
        • [1-3 tindakan paling penting]
        
        📞 Darurat: 112
        📝 No: [report_id]
        
        Tim sedang menindaklanjuti. Tetap aman!
        """
        
        urgency_level = extracted_info.get('urgency_level', 3)
        emergency_type = extracted_info.get('emergency_type', 'lainnya')
        location_info = extracted_info.get('location', {})
        immediate_actions = extracted_info.get('immediate_actions', [])
        
        # Prioritize actions based on urgency and type
        priority_actions = immediate_actions[:3] if immediate_actions else [
            "tetap tenang dan aman",
            "jauhi area berbahaya", 
            "tunggu bantuan"
        ]
        
        user_prompt = f"""
        DARURAT: {original_text}
        
        Data:
        - Jenis: {emergency_type}
        - Urgensi: {urgency_level}/5
        - Lokasi: {location_info.get('raw_location', 'tidak spesifik')}
        - Tindakan: {priority_actions}
        - No. Laporan: {report_id}
        
        Buat respons WhatsApp yang lengkap dalam 1 pesan saja.
        """
        
        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=300
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"Error generating optimized response: {e}")
            # Fallback response
            return f"""🚨 Laporan darurat {emergency_type} diterima

⚡ SEGERA:
• Tetap tenang dan pastikan keselamatan
• Jauhi area berbahaya
• Ikuti instruksi petugas jika ada

📞 Darurat: 112
📝 No: {report_id}

Tim sedang menindaklanjuti. Tetap aman!"""
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

    def generate_optimized_emergency_response(
        self,
        extracted_info: Dict,
        original_text: str,
        report_id: str,
        phone_number: str
    ) -> str:
        """Generate single optimized emergency response for WhatsApp sesuai regulasi"""

        system_prompt = """
        Anda adalah operator darurat profesional Indonesia. Berikan respons WhatsApp yang:

        1. SATU PESAN LENGKAP (maksimal 500 karakter)
        2. Struktur terorganisir dengan emoji sebagai separator
        3. Prioritas: konfirmasi → langkah segera → nomor darurat resmi → nomor laporan
        4. Bahasa Indonesia yang jelas, menenangkan, dan actionable
        5. Sesuai regulasi (Permenkominfo No.10/2016, Permenkes No.19/2016, UU No.24/2007)

        Format respons:
        🚨 [Konfirmasi singkat situasi]

        ⚡ SEGERA:
        • [1-3 tindakan penting sesuai konteks]
        • [Nomor darurat spesifik bila perlu]

        📞 Darurat: 112 (umum) · 119 (medis) · 110 (polisi)
        📝 No: [report_id]

        Tim resmi sedang menindaklanjuti. Tetap aman!
        """

        urgency_level = extracted_info.get("urgency_level", 3)
        emergency_type = extracted_info.get("emergency_type", "lainnya")
        location_info = extracted_info.get("location", {})
        immediate_actions = extracted_info.get("immediate_actions", [])

        # Tentukan tindakan prioritas (maks 3)
        priority_actions = immediate_actions[:3] if immediate_actions else [
            "Tetap tenang & amankan diri",
            "Jauhi sumber bahaya",
            "Hubungi 112 bila kondisi memburuk"
        ]

        user_prompt = f"""
        DARURAT: {original_text}

        Data:
        - Jenis: {emergency_type}
        - Urgensi: {urgency_level}/5
        - Lokasi: {location_info.get('raw_location', 'tidak spesifik')}
        - Tindakan prioritas: {priority_actions}
        - No. Laporan: {report_id}
        - Kontak pelapor: {phone_number}

        Buat respons WhatsApp 1 pesan sesuai format & regulasi di atas.
        """

        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=300
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"Error generating optimized response: {e}")
            # Pesan fallback bila API gagal
            return self.get_fallback_comprehensive_response(report_id, emergency_type, urgency_level)

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
export FLASK_APP=Lomba.py
export FLASK_ENV=production

# Load environment variables dari .env file
if [ -f .env ]; then
    echo "📋 Loading environment variables from .env..."
    export $(cat .env | xargs)
fi

# Test Supabase connection
echo "🔗 Testing Supabase connection..."
python -c "
from Lomba import SupabasePostgreSQLManager
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
python Lomba.py --mode server --port 5000 &

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


def create_app():
    """Factory function to create Flask app"""
    system = EnhancedEmergencyNLPSystem()
    return system.flask_app

# Create the app instance that Gunicorn can find
app = create_app()

CORS(app)
@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Render"""
    try:
        # Test database connection
        system = EnhancedEmergencyNLPSystem()
        conn = system.db_manager.get_connection()
        db_status = "connected" if conn else "disconnected"
        if conn:
            conn.close()
        
        # Test OpenAI API
        openai_status = "ok" if openai.api_key else "missing_key"
        
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.datetime.now().isoformat(),
            "database": db_status,
            "openai": openai_status,
            "environment": os.getenv("FLASK_ENV", "development"),
            "version": "1.0.0"
        }), 200
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.datetime.now().isoformat()
        }), 500

@app.route('/', methods=['GET'])
def index():
    """Root endpoint"""
    return jsonify({
        "service": "Emergency NLP System",
        "status": "running",
        "endpoints": {
            "webhook": "/webhook/whatsapp",
            "json_api": "/webhook/whatsapp/json", 
            "health": "/health",
            "reports": "/api/reports"
        }
    })

@app.route('/api/reports', methods=['GET'])
def get_reports():
    """API endpoint for reports"""
    try:
        system = EnhancedEmergencyNLPSystem()
        return system.get_reports_api()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

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
    
    # For development, run directly
    if len(sys.argv) > 1 and sys.argv[1] == '--dev':
        app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=True)
    else:
        # For production, let Gunicorn handle it
        main_enhanced()