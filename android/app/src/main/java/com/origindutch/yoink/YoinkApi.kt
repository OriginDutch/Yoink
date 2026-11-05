package com.origindutch.yoink

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

class HttpStatusException(val code: Int, message: String) : IOException(message)

/**
 * Blocking HTTP client for Yoink on the PC. Call from a background thread.
 * Plain HttpURLConnection on purpose: no extra libraries in the APK.
 */
class YoinkApi(private val pairing: Pairing) {

    var workingHost: String? = null
        private set
    private var baseUrl: String? = null

    /** Tries every address from the QR code and keeps the first one that answers. */
    fun connect(): String {
        var lastError: Exception? = null
        for (host in pairing.hosts) {
            val base = "http://$host:${pairing.port}"
            try {
                val conn = open("$base/api/info", "GET", connectTimeout = 2500, readTimeout = 5000)
                try {
                    check(conn)
                    val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                    baseUrl = base
                    workingHost = host
                    return json.optString("pc", pairing.pcName)
                } finally {
                    conn.disconnect()
                }
            } catch (e: HttpStatusException) {
                throw e // reachable but refused (wrong token): no point trying other hosts
            } catch (e: Exception) {
                lastError = e
            }
        }
        throw IOException("Yoink op de PC niet bereikbaar.", lastError)
    }

    /** Returns how many links the PC accepted. */
    fun sendLinks(urls: List<String>): Int {
        val base = baseUrl ?: throw IllegalStateException("connect() eerst aanroepen")
        val body = JSONObject().put("urls", JSONArray(urls)).toString().toByteArray()
        val conn = open("$base/api/links", "POST")
        try {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setFixedLengthStreamingMode(body.size)
            conn.outputStream.use { it.write(body) }
            check(conn)
            return JSONObject(conn.inputStream.bufferedReader().use { it.readText() }).optInt("accepted", urls.size)
        } finally {
            conn.disconnect()
        }
    }

    private fun open(url: String, method: String, connectTimeout: Int = 5000, readTimeout: Int = 15_000): HttpURLConnection {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = connectTimeout
        conn.readTimeout = readTimeout
        conn.useCaches = false
        conn.setRequestProperty(TOKEN_HEADER, pairing.token)
        return conn
    }

    private fun check(conn: HttpURLConnection) {
        val code = conn.responseCode
        if (code in 200..299) return
        val msg = when (code) {
            401 -> "Yoink kent deze telefoon niet (meer). Scan de QR-code opnieuw."
            else -> "PC gaf fout $code."
        }
        throw HttpStatusException(code, msg)
    }

    companion object {
        const val TOKEN_HEADER = "X-Yoink-Token"
    }
}
