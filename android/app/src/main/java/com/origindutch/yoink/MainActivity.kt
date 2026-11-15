package com.origindutch.yoink

import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlin.concurrent.thread

/** Pairing, the list of waiting links and a "send now" button. Nothing else. */
class MainActivity : ComponentActivity() {

    private lateinit var pairText: TextView
    private lateinit var statusText: TextView
    private lateinit var pendingHeader: TextView
    private lateinit var pendingList: TextView
    private lateinit var sendButton: Button
    private lateinit var clearButton: Button
    private lateinit var unpairButton: Button
    private var sending = false

    private val scanLauncher = registerForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@registerForActivityResult
        val parsed = PairingStore.parse(text)
        if (parsed == null) {
            Toast.makeText(this, "Dat is geen Yoink QR-code.", Toast.LENGTH_LONG).show()
            return@registerForActivityResult
        }
        PairingStore.save(this, parsed)
        statusText.text = "Gekoppeld met ${parsed.pcName} 🎉"
        refresh()
        sendNow()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildLayout())
        showLastError()
    }

    /** If the app crashed (or hit an error) last time, show it with a copy button. */
    private fun showLastError() {
        val text = CrashLog.take(this) ?: return
        val body = TextView(this).apply {
            this.text = text
            setTextIsSelectable(true)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setPadding(dp(20), dp(12), dp(20), dp(12))
        }
        AlertDialog.Builder(this)
            .setTitle("Yoink ging mis 💀")
            .setView(ScrollView(this).apply { addView(body) })
            .setPositiveButton("Kopiëren") { _, _ ->
                val cm = getSystemService(ClipboardManager::class.java)
                cm?.setPrimaryClip(ClipData.newPlainText("Yoink crash", text))
                CrashLog.clear(this)
                Toast.makeText(this, "Gekopieerd. Plak het bij Claude.", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("Weg ermee") { _, _ -> CrashLog.clear(this) }
            .setCancelable(false)
            .show()
    }

    override fun onResume() {
        super.onResume()
        refresh()
        // Opening the app is a good moment to retry.
        if (LinkStore.all(this).isNotEmpty() && PairingStore.load(this) != null) sendNow()
    }

    private fun buildLayout(): View {
        val pad = dp(20)
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        col.addView(TextView(this).apply {
            text = "Yoink"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 30f)
        })
        col.addView(TextView(this).apply {
            text = "Deel een link vanuit X, je browser of een andere app → Yoink. " +
                "Staat Yoink op je PC uit, dan wordt de link bewaard en later verstuurd."
            alpha = 0.75f
            setPadding(0, dp(6), 0, dp(16))
        })

        pairText = TextView(this).apply { setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f) }
        col.addView(pairText)
        statusText = TextView(this).apply {
            alpha = 0.75f
            setPadding(0, dp(4), 0, dp(12))
        }
        col.addView(statusText)

        col.addView(button("QR-code scannen") { scan() })
        sendButton = button("Nu versturen") { sendNow() }
        col.addView(sendButton)

        pendingHeader = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setPadding(0, dp(20), 0, dp(6))
        }
        col.addView(pendingHeader)
        pendingList = TextView(this).apply {
            alpha = 0.75f
            setTextIsSelectable(true)
        }
        col.addView(pendingList)
        clearButton = button("Wachtrij leegmaken") { confirmClear() }
        col.addView(clearButton)

        unpairButton = button("Ontkoppelen") { confirmUnpair() }
        col.addView(unpairButton, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = dp(28); gravity = Gravity.START })

        return ScrollView(this).apply {
            fitsSystemWindows = true
            addView(col)
        }
    }

    private fun refresh() {
        val pairing = PairingStore.load(this)
        pairText.text = if (pairing == null) "Niet gekoppeld. Open Yoink op je PC → Pair phone… en scan de QR-code."
        else "Gekoppeld met ${pairing.pcName}"
        unpairButton.visibility = if (pairing == null) View.GONE else View.VISIBLE

        val pending = LinkStore.all(this)
        pendingHeader.text = if (pending.isEmpty()) "Geen wachtende links" else "Wachtende links (${pending.size})"
        pendingList.text = pending.joinToString("\n\n")
        pendingList.visibility = if (pending.isEmpty()) View.GONE else View.VISIBLE
        clearButton.visibility = if (pending.isEmpty()) View.GONE else View.VISIBLE
        sendButton.isEnabled = !sending && pending.isNotEmpty() && pairing != null
    }

    private fun scan() {
        scanLauncher.launch(
            ScanOptions()
                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt("Scan de QR-code in Yoink op je PC (Pair phone…)")
                .setBeepEnabled(false)
                .setOrientationLocked(false)
        )
    }

    private fun sendNow() {
        if (sending) return
        sending = true
        statusText.text = "Versturen…"
        refresh()
        thread(name = "yoink-send") {
            val outcome = Sender.flush(applicationContext)
            runOnUiThread {
                sending = false
                if (outcome.result != Sender.Result.NOTHING) statusText.text = Sender.describe(outcome)
                else statusText.text = ""
                refresh()
            }
        }
    }

    private fun confirmClear() {
        AlertDialog.Builder(this)
            .setMessage("Alle wachtende links weggooien?")
            .setPositiveButton("Weggooien") { _, _ ->
                LinkStore.clear(this)
                RetryJob.update(this)
                statusText.text = ""
                refresh()
            }
            .setNegativeButton("Annuleren", null)
            .show()
    }

    private fun confirmUnpair() {
        AlertDialog.Builder(this)
            .setMessage("Ontkoppelen van deze PC?")
            .setPositiveButton("Ontkoppelen") { _, _ ->
                PairingStore.clear(this)
                RetryJob.update(this)
                statusText.text = ""
                refresh()
            }
            .setNegativeButton("Annuleren", null)
            .show()
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        setOnClickListener { onClick() }
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
