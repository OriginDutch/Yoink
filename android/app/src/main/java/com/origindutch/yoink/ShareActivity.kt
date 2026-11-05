package com.origindutch.yoink

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import kotlin.concurrent.thread

/**
 * Invisible screen behind "Share -> Yoink". Stores the link, tries to send it
 * right away, shows a toast and gets out of the way.
 */
class ShareActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = applicationContext

        val text = listOfNotNull(
            intent?.getStringExtra(Intent.EXTRA_TEXT),
            intent?.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString(),
        ).joinToString(" ")
        val urls = LinkStore.extract(text)

        if (urls.isEmpty()) {
            Toast.makeText(app, "Geen link gevonden in wat je deelde.", Toast.LENGTH_SHORT).show()
            finish()
            return
        }

        LinkStore.add(app, urls)

        if (PairingStore.load(app) == null) {
            Toast.makeText(app, "Bewaard 📦 Koppel eerst met Yoink op je PC.", Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            finish()
            return
        }

        finish()
        thread(name = "yoink-share") {
            val outcome = Sender.flush(app)
            Handler(Looper.getMainLooper()).post {
                Toast.makeText(app, Sender.describe(outcome), Toast.LENGTH_SHORT).show()
            }
        }
    }
}
