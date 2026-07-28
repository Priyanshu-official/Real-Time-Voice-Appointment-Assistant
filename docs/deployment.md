# Deployment & Public Exposure Guide

This guide details how to deploy the Real-Time Voice Appointment Assistant using Docker Compose and expose it publicly via Cloudflare Tunnel as specified in [ADR-006](architecture/adr-006-deployment-topology.md).

---

## 🏗️ Architecture Summary

Per **ADR-006**, the deployment topology consists of:
1. **Containerized Stack (`docker-compose.yml`)**:
   - `postgres`: PostgreSQL 16 database with `btree_gist` extension for concurrency-safe booking.
   - `api`: FastAPI application serving REST endpoints, Web Phone UI static assets, and the Pipecat real-time voice pipeline.
2. **Native Host Services (Mac)**:
   - `Ollama`: Runs natively on the Mac host (port 11434) to leverage Metal GPU acceleration for fast sub-2s LLM inference.
   - Reached from inside the Docker container via `http://host.docker.internal:11434`.
3. **Public Exposure**:
   - `Cloudflare Tunnel` (`cloudflared`): Exposes port `8000` to a secure public HTTPS/WSS URL without requiring port forwarding or cloud infrastructure.

---

## 📋 Prerequisites

- **macOS** with Docker Desktop installed and running.
- **Ollama** installed locally (`ollama run qwen2.5:7b-instruct-q4_K_M` or `ollama run llama3:8b`).
- **Cloudflare CLI (`cloudflared`)** installed locally (`brew install cloudflared`).

---

## 🚀 Quickstart Deployment Steps

### Step 1: Environment Configuration

Create a local `.env` file from `env.example`:

```bash
cp env.example .env
```

Ensure `.env` contains the required database credentials and Ollama settings:

```env
POSTGRES_USER=clinic_app
POSTGRES_PASSWORD=change-me-locally
POSTGRES_DB=clinic
DATABASE_URL=postgresql://clinic_app:change-me-locally@localhost:5432/clinic
OLLAMA_HOST=http://host.docker.internal:11434
OLLAMA_MODEL=qwen2.5:7b-instruct-q4_K_M
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b-instruct-q4_K_M
```

### Step 2: Start Native Ollama Engine

Ensure Ollama is running on your Mac host:

```bash
ollama serve
```

Verify that Ollama responds locally:

```bash
curl http://localhost:11434/api/tags
```

### Step 3: Launch Docker Compose Stack

Bring up both PostgreSQL and the API/Voice service:

```bash
docker compose up -d --build
```

Verify that both containers are running and healthy:

```bash
docker compose ps
```

*Output should show `clinic-postgres` (healthy) and `clinic-api` (running).*

### Step 4: Run Database Migrations & Seed Sample Data

Run database seeding inside the running `api` container:

```bash
docker compose exec api python -m app.db.seed
```

### Step 5: Verify Local Service Health & Host Connectivity

1. **Verify Health Endpoint**:
   ```bash
   curl http://localhost:8000/health
   ```
   *Expected Response: `{"status":"ok"}`*

2. **Verify Container-to-Ollama Host Connectivity**:
   ```bash
   docker compose exec api curl -s http://host.docker.internal:11434/api/tags
   ```

---

## 🌐 Cloudflare Tunnel Setup & Public Exposure

To allow callers to interact with the voice assistant from smartphones or external networks over secure HTTPS/WSS, use Cloudflare Tunnel.

### Option A: Quick Temporary Tunnel (Recommended for Demo / Testing)

Run `cloudflared` pointing directly to port `8000`:

```bash
cloudflared tunnel --url http://localhost:8000
```

#### Terminal Output Example:
```text
2026-07-28T22:00:00Z INF +-----------------------------------------------------------------------------------+
2026-07-28T22:00:00Z INF |  Your quick Tunnel has been created! Visit it at:                                 |
2026-07-28T22:00:00Z INF |  https://random-subdomain.trycloudflare.com                                       |
2026-07-28T22:00:00Z INF +-----------------------------------------------------------------------------------+
```

Copy the `https://<unique-id>.trycloudflare.com` URL provided in the terminal output.

---

### Option B: Named Production Cloudflare Tunnel

If you have a custom domain managed on Cloudflare:

1. **Authenticate Cloudflare CLI**:
   ```bash
   cloudflared tunnel login
   ```

2. **Create a Tunnel**:
   ```bash
   cloudflared tunnel create clinic-voice-tunnel
   ```

3. **Configure Routing**:
   ```bash
   cloudflared tunnel route dns clinic-voice-tunnel clinic.yourdomain.com
   ```

4. **Create Configuration File (`~/.cloudflared/config.yml`)**:
   ```yaml
   tunnel: clinic-voice-tunnel
   credentials-file: /Users/yourusername/.cloudflared/<tunnel-id>.json

   ingress:
     - hostname: clinic.yourdomain.com
       service: http://localhost:8000
     - service: http_status:404
   ```

5. **Run the Named Tunnel**:
   ```bash
   cloudflared tunnel run clinic-voice-tunnel
   ```

---

## 🧪 End-to-End Verification Checklist

Perform the following full manual test to confirm the deployment works end-to-end:

1. **Start Stack & Tunnel**:
   - `docker compose up -d`
   - `cloudflared tunnel --url http://localhost:8000`
2. **Access Public URL**:
   - Open the generated `https://<subdomain>.trycloudflare.com` URL in a browser on a mobile device or external Wi-Fi / cellular connection.
3. **Initiate Voice Call**:
   - Click **"Call Clinic"**.
   - Allow microphone permissions.
4. **Voice Flow Test**:
   - **Identity Challenge**: Speak *"Hello, my name is John Doe, DOB May 15 1985"*.
   - **Assistant Response**: Verify identity confirmation message.
   - **Appointment Inquiry**: Ask *"What time is my appointment?"*.
   - **Booking Test**: Ask *"Can I book an appointment with Dr. Sarah Connor for tomorrow at 10 AM?"*.
5. **Verify Audit Log & Concurrency**:
   - Inspect container logs: `docker compose logs -f api`
   - Confirm database writes and `audit_logs` entries created during the session.
