package com.zazaki.healthreader

import android.os.Bundle
import android.os.Environment
import android.widget.Button
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.gson.GsonBuilder
import com.samsung.android.sdk.health.data.HealthDataService
import com.samsung.android.sdk.health.data.HealthDataStore
import com.samsung.android.sdk.health.data.data.HealthDataPoint
import com.samsung.android.sdk.health.data.data.entries.HeartRate
import com.samsung.android.sdk.health.data.data.entries.SleepSession
import com.samsung.android.sdk.health.data.permission.AccessType
import com.samsung.android.sdk.health.data.permission.Permission
import com.samsung.android.sdk.health.data.request.DataType
import com.samsung.android.sdk.health.data.request.DataTypes
import com.samsung.android.sdk.health.data.request.LocalTimeFilter
import com.samsung.android.sdk.health.data.request.Ordering
import kotlinx.coroutines.*
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

class MainActivity : AppCompatActivity() {

    data class HrSample(val t: String, val hr: Float)
    data class SleepStageRec(val type: String, val start: String, val end: String)
    data class SleepSessionRec(val start: String, val end: String, val stages: List<SleepStageRec>)
    data class SleepRec(val t: String, val score: Int?, val duration_s: Long?, val sessions: List<SleepSessionRec>?)
    data class ExportFile(
        val exported_at: String,
        val tz: String,
        val days: Int,
        val heart_rate: List<HrSample>,
        val sleep: List<SleepRec>
    )

    private lateinit var logView: TextView
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val scroll = ScrollView(this)
        logView = TextView(this)
        logView.textSize = 13f
        logView.text = "HealthReader export v0.2\n"
        scroll.addView(logView)

        val btnRead = Button(this)
        btnRead.text = "READ (count check)"
        btnRead.setOnClickListener { readAll() }

        val btnExport = Button(this)
        btnExport.text = "EXPORT full data JSON"
        btnExport.setOnClickListener { exportAll() }

        val btnSend = Button(this)
        btnSend.text = "EXPORT + SEND to PC"
        btnSend.setOnClickListener { exportAll(sendToPc = true) }

        val layout = android.widget.LinearLayout(this)
        layout.orientation = android.widget.LinearLayout.VERTICAL
        layout.setPadding(0, statusBarHeight(), 0, 0)
        layout.addView(btnRead, android.widget.LinearLayout.LayoutParams(
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
            android.widget.LinearLayout.LayoutParams.WRAP_CONTENT))
        layout.addView(btnExport, android.widget.LinearLayout.LayoutParams(
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
            android.widget.LinearLayout.LayoutParams.WRAP_CONTENT))
        layout.addView(btnSend, android.widget.LinearLayout.LayoutParams(
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
            android.widget.LinearLayout.LayoutParams.WRAP_CONTENT))
        layout.addView(scroll, android.widget.LinearLayout.LayoutParams(
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT, 1f))
        setContentView(layout)
        log("ready")
    }

    private fun statusBarHeight(): Int {
        val resId = resources.getIdentifier("status_bar_height", "dimen", "android")
        return if (resId > 0) resources.getDimensionPixelSize(resId) else 0
    }

    private fun log(s: String) { runOnUiThread { logView.append("\n$s") } }

    private suspend fun ensureStoreAndPerms(): HealthDataStore? {
        val store = try { HealthDataService.getStore(applicationContext) }
        catch (e: Throwable) { log("getStore FAILED: ${e.javaClass.simpleName}: ${e.message}"); return null }
        val want = setOf(
            Permission.of(DataTypes.HEART_RATE, AccessType.READ),
            Permission.of(DataTypes.SLEEP, AccessType.READ)
        )
        return try {
            val granted = store.requestPermissions(want, this)
            log("granted ${granted.size}/${want.size}")
            if (granted.containsAll(want)) store else null
        } catch (e: Throwable) {
            log("requestPermissions: ${e.javaClass.simpleName}: ${e.message}")
            store // best-effort
        }
    }

    private fun readAll() = scope.launch {
        val store = ensureStoreAndPerms() ?: return@launch
        log("HR: ${readPoints(store, DataTypes.HEART_RATE, 1000).size} records (≤1000 window)")
        log("Sleep: ${readPoints(store, DataTypes.SLEEP, 100).size} records")
    }

    private fun exportAll(sendToPc: Boolean = false) = scope.launch(Dispatchers.IO) {
        val store = ensureStoreAndPerms() ?: return@launch
        log("export: day-window streaming over 90 days...")
        val tz = ZoneId.systemDefault()
        val today = LocalDate.now(tz)
        val dir = getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS)
            ?: File(filesDir, "exports")
        dir.mkdirs()
        val f = File(dir, "health_export_90d.json")
        var hrCount = 0
        var sleepCount = 0

        val writer = java.io.BufferedWriter(
            java.io.OutputStreamWriter(f.outputStream(), Charsets.UTF_8))
        val jw = com.google.gson.stream.JsonWriter(writer)
        jw.setIndent("  ")
        jw.beginObject()
        jw.name("exported_at").value(Instant.now().toString())
        jw.name("tz").value(tz.id)
        jw.name("days").value(90)

        jw.name("heart_rate")
        jw.beginArray()
        for (i in 0 until 90) {
            val start = today.minusDays(i + 1L).atStartOfDay()
            val end = today.minusDays(i.toLong()).atStartOfDay()
            try {
                val dps = readDayPoints(store, DataTypes.HEART_RATE, start, end)
                val seen = HashSet<String>()
                for (dp in dps) {
                    val series = getField<List<HeartRate>>(DataTypes.HEART_RATE, "SERIES_DATA", dp)
                    if (series != null) {
                        for (s in series) {
                            if (seen.add(s.startTime.toString())) {
                                jw.beginObject()
                                jw.name("t").value(s.startTime.toString())
                                jw.name("hr").value(s.heartRate.toDouble())
                                jw.endObject()
                                hrCount++
                            }
                        }
                    } else {
                        val hr = getField<Number>(DataTypes.HEART_RATE, "HEART_RATE", dp)
                        val st = dpStart(dp)
                        if (hr != null && st != null && seen.add(st.toString())) {
                            jw.beginObject()
                            jw.name("t").value(st.toString())
                            jw.name("hr").value(hr.toFloat().toDouble())
                            jw.endObject()
                            hrCount++
                        }
                    }
                }
            } catch (e: Throwable) { /* skip bad day */ }
            if (i % 15 == 14) log("hr day ${i + 1}/90 so far=$hrCount")
        }
        jw.endArray()
        log("HR samples: $hrCount")

        jw.name("sleep")
        jw.beginArray()
        for (i in 0 until 90) {
            val start = today.minusDays(i + 1L).atStartOfDay()
            val end = today.minusDays(i.toLong()).atStartOfDay()
            try {
                val dps = readDayPoints(store, DataTypes.SLEEP, start, end)
                for (dp in dps) {
                    val st = dpStart(dp) ?: continue
                    val sessions = getField<List<SleepSession>>(DataTypes.SLEEP, "SESSIONS", dp)
                    val score = getField<Int>(DataTypes.SLEEP, "SLEEP_SCORE", dp)
                    val dur = getField<java.time.Duration>(DataTypes.SLEEP, "DURATION", dp)
                    jw.beginObject()
                    jw.name("t").value(st.toString())
                    if (score != null) jw.name("score").value(score)
                    if (dur != null) jw.name("duration_s").value(dur.seconds)
                    jw.name("sessions")
                    jw.beginArray()
                    sessions?.forEach { sess ->
                        jw.beginObject()
                        jw.name("start").value(sess.startTime.toString())
                        jw.name("end").value(sess.endTime.toString())
                        jw.name("stages")
                        jw.beginArray()
                        sess.stages?.forEach { stage ->
                            jw.beginObject()
                            jw.name("type").value(stage.stage.name)
                            jw.name("start").value(stage.startTime.toString())
                            jw.name("end").value(stage.endTime.toString())
                            jw.endObject()
                        }
                        jw.endArray()
                        jw.endObject()
                    }
                    jw.endArray()
                    jw.endObject()
                    sleepCount++
                }
            } catch (e: Throwable) { /* skip bad day */ }
            if (i % 15 == 14) log("sleep day ${i + 1}/90 so far=$sleepCount")
        }
        jw.endArray()
        log("Sleep records: $sleepCount")

        jw.endObject()
        jw.close()
        log("WROTE: ${f.absolutePath} (${f.length()} bytes) hr=$hrCount sleep=$sleepCount")
        if (sendToPc) {
            uploadToPc(f)
        }
    }

    /** POST a JSON file's raw body to the PC receiver (Windows host 192.168.137.1:8899). */
    private suspend fun uploadToPc(file: File) {
        log("upload: POST ${file.name} -> http://192.168.137.1:8899/upload ...")
        try {
            val body = file.readBytes()
            val conn = java.net.URL("http://192.168.137.1:8899/upload").openConnection()
            conn as java.net.HttpURLConnection
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 10000
            conn.readTimeout = 30000
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Content-Length", body.size.toString())
            conn.outputStream.use { it.write(body) }
            val code = conn.responseCode
            val resp = if (code in 200..299) conn.inputStream.bufferedReader().readText()
                        else conn.errorStream?.bufferedReader()?.readText() ?: ""
            log("upload done: HTTP $code  ${resp.take(200)}")
            conn.disconnect()
        } catch (e: Throwable) {
            log("upload FAILED: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    /** Read all datapoints within one local-day window [startLdt, endLdt). */
    @Suppress("UNCHECKED_CAST")
    private suspend fun readDayPoints(
        store: HealthDataStore,
        dataType: DataType,
        startLdt: java.time.LocalDateTime,
        endLdt: java.time.LocalDateTime
    ): List<HealthDataPoint> {
        return try {
            val builder = dataType.javaClass.getMethod("getReadDataRequestBuilder").invoke(dataType)
            val bc = builder.javaClass
            try { bc.getMethod("setLimit", Int::class.java).invoke(builder, 20000) } catch (_: Throwable) {}
            try {
                bc.getMethod("setLocalTimeFilter", LocalTimeFilter::class.java)
                    .invoke(builder, LocalTimeFilter.of(startLdt, endLdt))
            } catch (_: Throwable) {}
            val req = bc.getMethod("build").invoke(builder) as com.samsung.android.sdk.health.data.request.ReadDataRequest<HealthDataPoint>
            val resp = store.readData(req)
            (resp.dataList as? List<HealthDataPoint>) ?: emptyList()
        } catch (e: Throwable) {
            log("readDayPoints err ${dataType}: ${e.javaClass.simpleName} ${e.message}")
            emptyList()
        }
    }

    private suspend fun readPoints(store: HealthDataStore, dataType: DataType, limit: Int): List<HealthDataPoint> {
        val req = buildReadRequest(dataType, limit, null) ?: return emptyList()
        val resp = store.readData(req)
        @Suppress("UNCHECKED_CAST")
        return (resp.dataList as? List<HealthDataPoint>) ?: emptyList()
    }

    @Suppress("UNCHECKED_CAST")
    private fun buildReadRequest(
        dataType: DataType,
        limit: Int,
        since: Instant?
    ): com.samsung.android.sdk.health.data.request.ReadDataRequest<HealthDataPoint>? {
        return try {
            val builder = dataType.javaClass.getMethod("getReadDataRequestBuilder").invoke(dataType)
            val bc = builder.javaClass
            try { bc.getMethod("setLimit", Int::class.java).invoke(builder, limit) } catch (_: Throwable) {}
            try { bc.getMethod("setOrdering", Ordering::class.java).invoke(builder, Ordering.DESC) } catch (_: Throwable) {}
            if (since != null) {
                try {
                    val start = java.time.LocalDateTime.ofInstant(since, ZoneId.systemDefault())
                    bc.getMethod("setLocalTimeFilter", LocalTimeFilter::class.java)
                        .invoke(builder, LocalTimeFilter.since(start))
                } catch (_: Throwable) {}
            }
            bc.getMethod("build").invoke(builder) as com.samsung.android.sdk.health.data.request.ReadDataRequest<HealthDataPoint>?
        } catch (e: Throwable) {
            log("buildReadRequest err ${dataType}: ${e.javaClass.simpleName} ${e.message}")
            null
        }
    }

    @Suppress("UNCHECKED_CAST")
    private fun <T> getField(dataType: DataType, fieldName: String, dp: HealthDataPoint): T? {
        return try {
            val field = dataType.javaClass.getField(fieldName).get(dataType)
            dp.javaClass.getMethod("getValue", com.samsung.android.sdk.health.data.data.Field::class.java)
                .invoke(dp, field) as? T
        } catch (_: Throwable) { null }
    }

    private fun dpStart(dp: HealthDataPoint): Instant? {
        return try { dp.javaClass.getMethod("getStartTime").invoke(dp) as? Instant }
        catch (_: Throwable) { null }
    }

    override fun onDestroy() { scope.cancel(); super.onDestroy() }
}
