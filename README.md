# 🚨 Emergency NLP System - Dokumentasi

Sistem NLP Darurat ini adalah aplikasi berbasis AI yang dirancang untuk menerima, menganalisis, dan menanggapi laporan kejadian darurat melalui WhatsApp dan input suara langsung. Sistem ini menggunakan OpenAI GPT-4, Twilio WhatsApp API, dan Supabase PostgreSQL sebagai database utama.

---

## 📋 Daftar Isi

- [Fitur Utama](#-fitur-utama)
- [Arsitektur Sistem](#-arsitektur-sistem)
- [Prasyarat](#-prasyarat)
- [Setup Environment](#-setup-environment)
- [Setup Ngrok](#-setup-ngrok)
- [Setup Twilio WhatsApp](#-setup-twilio-whatsapp)
- [Konfigurasi Supabase PostgreSQL](#-konfigurasi-supabase-postgresql)
- [Instalasi dan Menjalankan Sistem](#-instalasi-dan-menjalankan-sistem)
- [Mode Operasi](#-mode-operasi)
- [Struktur API](#-struktur-api)
- [Dashboard Analytics](#-dashboard-analytics)
- [Troubleshooting](#-troubleshooting)
- [Lisensi](#-lisensi)

---

## ✅ Fitur Utama

- 📱 **Integrasi WhatsApp** via Twilio API  
- 🔊 **Pengenalan Suara** (Speech-to-Text) dengan Google & Whisper  
- 💬 **Analisis Darurat** menggunakan GPT-4  
- 🗄️ **Database Terpusat** dengan Supabase PostgreSQL  
- 📊 **Dashboard Analitik Real-time**  
- 📲 **Webhook Server** untuk menerima pesan WhatsApp  
- 📝 **Struktur Laporan Terstandar** (JSON + Enum)

---

## 🏗️ Arsitektur Sistem
[WhatsApp User]
↓ (pesan teks/suara)
[Twilio API Gateway]
↓
[Ngrok Tunnel] → [Flask Webhook]
↓
[Emergency NLP System]
├── OpenAI GPT-4 (analisis darurat)
├── Supabase PostgreSQL (penyimpanan data)
├── Twilio (respons WhatsApp)
└── SpeechRecognition (audio → teks)


---

## 🔧 Prasyarat

Pastikan Anda telah menginstal:

- [Python 3.8+](https://www.python.org/downloads/)
- [pip](https://pip.pypa.io/en/stable/installation/)
- [Ngrok](https://ngrok.com/download) (untuk tunneling lokal)
- Akun [Twilio](https://twilio.com)
- Akun [Supabase](https://supabase.com)

---

## 🛠️ Setup Environment

### 1. Clone Repository

```bash
git clone https://github.com/nama-anda/emergency-nlp-system.git
cd emergency-nlp-system

python -m venv venv
source venv/bin/activate  # Linux/Mac
# atau
venv\Scripts\activate     # Windows

pip install -r requirements_supabase.txt
```


### 2. Download dan Install Ngrok
- Kunjungi: https://ngrok.com/download
- Ekstrak dan tambahkan ke PATH, atau letakkan di direktori proyek.

```bash
./ngrok config add-authtoken <YOUR_AUTH_TOKEN>
./ngrok http 5000


# Forwarding  https://abc123.ngrok.io -> http://localhost:5000
# catat url public
```

# 🚨 Emergency NLP System with Twilio WhatsApp & Supabase

Sistem ini memungkinkan pelaporan darurat melalui **WhatsApp** (Twilio) dan input suara/teks. Data laporan tersimpan di **Supabase PostgreSQL**, serta dilengkapi dengan **Dashboard Analytics**.

---

## 📦 Setup Twilio WhatsApp

### 1. Daftar Akun Twilio
- Kunjungi: [https://twilio.com](https://twilio.com)  
- Buat akun gratis.

### 2. Aktifkan WhatsApp Sandbox
- Login ke **Twilio Console**  
- Pergi ke: **Explore Products → Programmable Messaging → WhatsApp**  
- Klik **Get Started → Try WhatsApp Sandbox**  
- Ikuti instruksi untuk menghubungkan nomor WhatsApp Anda.

### 3. Setel Webhook
Di halaman **Sandbox**, cari bagian **When a message comes in**  
Isi dengan URL dari Ngrok:


Method: **HTTP POST**

### 4. Catat Konfigurasi
Simpan informasi berikut di file `.env`:

- Account SID  
- Auth Token  
- WhatsApp Number (biasanya `whatsapp:+14155238886`)

---

## 🗄️ Konfigurasi Supabase PostgreSQL

### 1. Buat Proyek di Supabase
- Kunjungi: [https://supabase.com](https://supabase.com)  
- Buat proyek baru.  
- Tunggu hingga selesai provisioning.

### 2. Dapatkan Database URL
- Buka **Project Settings → Database**  
- Salin **Connection String** (format `postgres://...`)

### 3. Buat Schema dan Tabel
Sistem akan otomatis membuat:

- Schema: `dev_moklet_ai`  
- Tabel: `emergency_reports`, `whatsapp_conversations`  

Pastikan ekstensi `uuid-ossp` aktif.

---

## ⚙️ Instalasi dan Menjalankan Sistem

### 1. Buat File Environment
```bash
cp .env.supabase.template .env
```

### 2. Edit file env
# OpenAI
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886

# Supabase PostgreSQL
DATABASE_URL=postgres://username:password@db.yourproject.supabase.co:5432/postgres

# Flask
FLASK_DEBUG=False


```bash
python Lomba.py
```
