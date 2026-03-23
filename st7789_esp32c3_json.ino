// Objetivo: exibir métricas do servidor (CPU temp/load, RAM, disk, uptime) no ST7789 320x240
// Técnica: Adafruit ST7789 + HTTPClient + ArduinoJson + NTP
//          Pinagem ESP32-C3 Supermini conforme User_Setup.h do TFT_eSPI
//          Reconexão WiFi automática com feedback visual
//          Feedback explícito para falha de WiFi e falha de servidor

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>
#include <time.h>
#include "senha.h"

// --- Pinagem ESP32-C3 Supermini (conforme User_Setup.h) ---
#define TFT_CS    20
#define TFT_DC    10
#define TFT_RST    9
#define TFT_MOSI   6
#define TFT_SCLK   7

Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_MOSI, TFT_SCLK, TFT_RST);

// --- Config ---
const long  POLL_MS    = 30000;
const char* NTP_SERVER = "pool.ntp.org";
const long  GMT_OFFSET = -4 * 3600;  // UTC-3, ajuste conforme fuso
const int   DST_OFFSET = 0;

// --- Paleta de cores ---
#define C_BG      0x0000  // preto
#define C_HEADER  0x0210  // azul escuro
#define C_TITLE   0x07FF  // ciano
#define C_LABEL   0xC618  // cinza claro
#define C_VALUE   0xFFFF  // branco
#define C_OK      0x07E0  // verde
#define C_WARN    0xFFE0  // amarelo
#define C_ERR     0xF800  // vermelho
#define C_BORDER  0x4208  // cinza escuro
#define C_DIM     0x632C  // cinza médio (valores secundários)

// --- Estrutura de dados ---
struct Stats {
    float cpu_temp;
    float cpu_load;
    float mem_load;
    float disk;
    long  uptime;
    bool  valid;
};

Stats lastStats  = {0, 0, 0, 0, 0, false};
unsigned long lastPoll    = 0;
unsigned long lastClockUp = 0;
bool wifiOk = false;

// ─────────────────────────────────────────────
// Utilitários de cor
// ─────────────────────────────────────────────
uint16_t corMetrica(float pct, float warnThr, float errThr) {
    if (pct < warnThr) return C_OK;
    if (pct < errThr)  return C_WARN;
    return C_ERR;
}

// ─────────────────────────────────────────────
// Barra de status (rodapé — linha y=220)
// ─────────────────────────────────────────────
void drawStatus(const char* msg, uint16_t cor) {
    tft.fillRect(0, 218, 320, 22, C_BG);
    tft.drawFastHLine(0, 217, 320, C_BORDER);
    tft.setTextColor(cor);
    tft.setTextSize(1);
    tft.setCursor(4, 225);
    tft.print(msg);
}

// ─────────────────────────────────────────────
// WiFi
// ─────────────────────────────────────────────
bool conectarWifi() {
    WiFi.disconnect(true);
    delay(200);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    char msg[64];
    snprintf(msg, sizeof(msg), "WiFi: conectando a %s...", WIFI_SSID);
    drawStatus(msg, C_WARN);

    for (int t = 0; t < 20; t++) {
        if (WiFi.status() == WL_CONNECTED) {
            snprintf(msg, sizeof(msg), "WiFi OK  %s", WiFi.localIP().toString().c_str());
            drawStatus(msg, C_OK);
            return true;
        }
        delay(500);
    }

    drawStatus("WiFi: FALHA na conexao", C_ERR);
    return false;
}

void garantirWifi() {
    if (WiFi.status() != WL_CONNECTED) {
        wifiOk = conectarWifi();
    }
}

// ─────────────────────────────────────────────
// NTP
// ─────────────────────────────────────────────
String getHora() {
    struct tm ti;
    if (!getLocalTime(&ti, 100)) return "--:--:--";
    char buf[9];
    strftime(buf, sizeof(buf), "%H:%M", &ti);
    return String(buf);
}

String getData() {
    struct tm ti;
    if (!getLocalTime(&ti, 100)) return "--/--/----";
    char buf[11];
    strftime(buf, sizeof(buf), "%d/%m/%Y", &ti);
    return String(buf);
}

// ─────────────────────────────────────────────
// Componentes de display
// ─────────────────────────────────────────────
void drawBarra(int x, int y, int w, int h, float pct, uint16_t cor) {
    // limita pct ao range 0-100
    if (pct < 0)   pct = 0;
    if (pct > 100) pct = 100;
    tft.drawRect(x, y, w, h, C_BORDER);
    int fill = (int)((pct / 100.0f) * (w - 2));
    tft.fillRect(x + 1,        y + 1, fill,          h - 2, cor);
    tft.fillRect(x + 1 + fill, y + 1, (w-2) - fill,  h - 2, C_BG);
}

// Bloco de métrica com barra — ocupa 52px de altura
// y: topo do bloco
void drawBlocoMetrica(int y, const char* label, float valor,
                       const char* unidade, float warnThr, float errThr) {
    uint16_t cor = corMetrica(valor, warnThr, errThr);

    // Label pequeno
    tft.setTextColor(C_LABEL);
    tft.setTextSize(1);
    tft.setCursor(4, y);
    tft.print(label);

    // Valor grande alinhado à direita
    char vbuf[12];
    snprintf(vbuf, sizeof(vbuf), "%.1f%s", valor, unidade);
    tft.setTextColor(cor);
    tft.setTextSize(2);
    // calcula posição X para alinhar à direita (cada char ~12px no size 2)
    int vx = 320 - (strlen(vbuf) * 12) - 4;
    tft.setCursor(vx, y - 2);
    tft.print(vbuf);

    // Barra
    drawBarra(4, y + 14, 312, 14, valor, cor);
}

String formatUptime(long s) {
    long d = s / 86400;
    long h = (s % 86400) / 3600;
    long m = (s % 3600) / 60;
    char buf[16];
    if (d > 0)
        snprintf(buf, sizeof(buf), "%ldd %ldh%02ldm", d, h, m);
    else
        snprintf(buf, sizeof(buf), "%ldh%02ldm", h, m);
    return String(buf);
}

// ─────────────────────────────────────────────
// Cabeçalho (estático — desenhado uma vez)
// ─────────────────────────────────────────────
void drawCabecalho() {
    tft.fillRect(0, 0, 320, 32, C_HEADER);
    tft.setTextColor(C_TITLE);
    tft.setTextSize(2);
    tft.setCursor(6, 8);
    tft.print("SERVER MONITOR");
    tft.drawFastHLine(0, 32, 320, C_BORDER);
}

// ─────────────────────────────────────────────
// Atualiza relógio (somente área do relógio)
// ─────────────────────────────────────────────
void drawRelogio() {
    tft.fillRect(190, 2, 128, 28, C_HEADER);
    tft.setTextColor(C_VALUE);
    tft.setTextSize(1);
    tft.setCursor(192, 6);
    tft.print(getData());
    tft.setCursor(200, 18);
    tft.print(getHora());
}

// ─────────────────────────────────────────────
// Tela de erro — servidor inacessível
// ─────────────────────────────────────────────
void drawErroServidor() {
    tft.fillRect(0, 34, 320, 182, C_BG);
    tft.setTextColor(C_ERR);
    tft.setTextSize(2);
    tft.setCursor(50, 90);
    tft.print("SEM DADOS");
    tft.setTextSize(1);
    tft.setTextColor(C_LABEL);
    tft.setCursor(20, 120);
    tft.print("Servidor Flask inacessivel");
    tft.setCursor(20, 135);
    tft.print("Aguardando proxima tentativa...");
}

// ─────────────────────────────────────────────
// Tela principal de métricas
// ─────────────────────────────────────────────
void drawMetricas(Stats& st) {
    tft.fillRect(0, 34, 320, 182, C_BG);

    // --- 3 blocos com barra (52px cada, espaço 4px entre) ---
    // Bloco 1: CPU TEMP  (warn >70, err >85 °C — tratar como % visual 0-100)
    drawBlocoMetrica(38,  "CPU TEMP (C)",  st.cpu_temp, "C",  70, 85);

    // Bloco 2: CPU LOAD
    drawBlocoMetrica(94,  "CPU LOAD",      st.cpu_load, "%",  60, 85);

    // Bloco 3: MEM LOAD
    drawBlocoMetrica(150, "MEM LOAD",      st.mem_load, "%",  70, 88);

    // --- Linha divisória ---
    tft.drawFastHLine(0, 178, 320, C_BORDER);

    // --- Valores secundários: DISCO e UPTIME (sem barra, texto compacto) ---
    tft.setTextSize(1);

    tft.setTextColor(C_DIM);
    tft.setCursor(4, 184);
    tft.print("DISCO:");
    tft.setTextColor(corMetrica(st.disk, 75, 90));
    tft.setCursor(50, 184);
    char dbuf[10];
    snprintf(dbuf, sizeof(dbuf), "%.1f%%", st.disk);
    tft.print(dbuf);

    tft.setTextColor(C_DIM);
    tft.setCursor(120, 184);
    tft.print("UPTIME:");
    tft.setTextColor(C_VALUE);
    tft.setCursor(172, 184);
    tft.print(formatUptime(st.uptime));
}

// ─────────────────────────────────────────────
// HTTP polling
// ─────────────────────────────────────────────
bool fetchStats(Stats& st) {
    HTTPClient http;
    http.begin(SERVER_URL);
    http.setTimeout(5000);
    int code = http.GET();

    if (code != 200) {
        http.end();
        return false;
    }

    String payload = http.getString();
    http.end();

    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, payload);
    if (err) return false;

    st.cpu_temp = doc["cpu_temp"] | 0.0f;
    st.cpu_load = doc["cpu_load"] | 0.0f;
    st.mem_load = doc["mem_load"] | 0.0f;
    st.disk     = doc["disk"]     | 0.0f;
    st.uptime   = doc["uptime"]   | 0L;
    st.valid    = true;
    return true;
}

// ─────────────────────────────────────────────
// Setup
// ─────────────────────────────────────────────
void setup() {
    Serial.begin(115200);

    // Init display — modo landscape, 320x240
    tft.init(240, 320);
    tft.setRotation(1);
    tft.fillScreen(C_BG);

    // Splash
    tft.setTextColor(C_TITLE);
    tft.setTextSize(2);
    tft.setCursor(60, 100);
    tft.print("Iniciando...");

    wifiOk = conectarWifi();

    if (wifiOk) {
        configTime(GMT_OFFSET, DST_OFFSET, NTP_SERVER);
        delay(1500);
    }

    tft.fillScreen(C_BG);
    drawCabecalho();
    drawRelogio();
}

// ─────────────────────────────────────────────
// Loop
// ─────────────────────────────────────────────
void loop() {
    // Tick do relógio a cada 1s
    if (millis() - lastClockUp >= 30000) {
        lastClockUp = millis();
        drawRelogio();
    }

    // Poll a cada 30s (ou imediatamente na primeira iteração)
    if (millis() - lastPoll >= POLL_MS || lastPoll == 0) {
        lastPoll = millis();

        garantirWifi();

        bool ok = false;
        if (wifiOk) ok = fetchStats(lastStats);

        if (!ok) {
            lastStats.valid = false;
            drawErroServidor();
            drawStatus("ERRO: Flask inacessivel", C_ERR);
        } else {
            drawMetricas(lastStats);
            char ip[40];
            snprintf(ip, sizeof(ip), "WiFi OK  %s", WiFi.localIP().toString().c_str());
            drawStatus(ip, C_OK);
        }
    }

    delay(50);
}