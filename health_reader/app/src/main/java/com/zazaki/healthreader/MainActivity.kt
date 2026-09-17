package com.zazaki.healthreader

import android.Manifest
import android.app.TimePickerDialog
import android.content.Context
import android.os.Bundle
import android.os.Build
import android.view.LayoutInflater
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.samsung.android.sdk.health.data.HealthDataService
import com.samsung.android.sdk.health.data.HealthDataStore
import com.samsung.android.sdk.health.data.data.AggregatedData
import com.samsung.android.sdk.health.data.data.AggregateOperation
import com.samsung.android.sdk.health.data.data.HealthDataPoint
import com.samsung.android.sdk.health.data.data.entries.BloodGlucose
import com.samsung.android.sdk.health.data.data.entries.ExerciseSession
import com.samsung.android.sdk.health.data.data.entries.HeartRate
import com.samsung.android.sdk.health.data.data.entries.OxygenSaturation
import com.samsung.android.sdk.health.data.data.entries.SkinTemperature
import com.samsung.android.sdk.health.data.data.entries.SleepSession
import com.samsung.android.sdk.health.data.permission.AccessType
import com.samsung.android.sdk.health.data.permission.Permission
import com.samsung.android.sdk.health.data.request.AggregateRequest
import com.samsung.android.sdk.health.data.request.DataType
import com.samsung.android.sdk.health.data.request.DataTypes
import com.samsung.android.sdk.health.data.request.LocalTimeFilter
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlin.coroutines.Continuation
import kotlin.coroutines.resume
import java.io.File
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/**
 * HealthReader v2 主界面（任务 A ①-⑥）。
 *
 * - ① 手动一键传输昨日：主按钮 📤 传输昨日 (MM-DD) → 单日导出 YYYYMMDD_sleep_hr.json → POST
 *   http://192.168.137.1:8899/upload → 2xx 标记 sent → 刷新 UI + 日历事件更新。
 * - ② 增量控制/补传：sync_state.json 状态机（SyncStateStore）；横幅「有 N 天未传输」+ 全部补传。
 * - ③ 日期状态表：windowStart..today（最多 45 行，最新在上），今日「待同步」、sent/pending/nodata 徽标。
 * - ④ 日历联动（CalendarSync）：专属日历 健康数据同步，按 (calendarId, 日期) 幂等 upsert 每日 all-day 事件。
 * - ⑤ 提醒：打开时去重通知 + 每日提醒（ReminderScheduler：一次性精确闹钟 + Receiver 触发后自续排）。
 * - ⑥ 绑定 designer 布局 activity_main.xml / item_day_row.xml。
 *
 * SHealth 读取辅助函数沿用 v0.2 已验证的反射模式（readDayPoints 逐日窗口 / getField / dpStart / 流式 JsonWriter）。
 */
class MainActivity : AppCompatActivity() {

    // ---------- 视图 ----------
    private lateinit var llRoot: LinearLayout
    private lateinit var tvSubtitle: TextView
    private lateinit var tvStatToday: TextView
    private lateinit var tvStatConsec: TextView
    private lateinit var tvStatTotal: TextView
    private lateinit var llPendingBanner: LinearLayout
    private lateinit var tvPendingBanner: TextView
    private lateinit var btnBackfill: Button
    private lateinit var tvDaySectionTitle: TextView
    private lateinit var llDayList: LinearLayout
    private lateinit var llFeedback: LinearLayout
    private lateinit var tvFeedback: TextView
    private lateinit var ivFeedbackCheck: ImageView
    private lateinit var btnPrimary: Button
    private lateinit var btnSettings: Button

    // ---------- 状态 ----------
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var stateFile: SyncStateStore.StateFile = SyncStateStore.StateFile()
    private var healthStore: HealthDataStore? = null
    private var today: LocalDate = LocalDate.now()
    private var windowStart: LocalDate = today
    private var running = false          // 传输/补传进行中
    private var initialized = false      // 首次扫描完成前禁用操作按钮
    private val rowViews = HashMap<LocalDate, DayRowHolder>()

    // 权限续延（suspendCancellableCoroutine 桥接 launcher 回调）。
    // 字段类型用 kotlin.coroutines.Continuation（而非 CancellableContinuation），
    // 使 .resume(value) 解析为单参扩展函数（coroutines 1.9.0 的 CancellableContinuation 只有带 onCancellation 的成员）。
    private var calendarPermCont: Continuation<Boolean>? = null
    private var notifPermCont: Continuation<Boolean>? = null

    private val calendarPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { result ->
        val granted = (result[Manifest.permission.READ_CALENDAR] == true) &&
                (result[Manifest.permission.WRITE_CALENDAR] == true)
        calendarPermCont?.resume(granted)
        calendarPermCont = null
    }

    private val notifPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        notifPermCont?.resume(granted)
        notifPermCont = null
    }

    private class DayRowHolder(val tvDate: TextView, val tvLabel: TextView, val tvStatus: TextView)

    private class PrepResult(val file: File?, val noStore: Boolean)

    // ---------- 格式化 ----------
    private val mdFmt: DateTimeFormatter = DateTimeFormatter.ofPattern("MM-dd")
    private val mdhmFmt: DateTimeFormatter = DateTimeFormatter.ofPattern("MM-dd HH:mm")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        bindViews()
        applyEdgeToEdge()

        btnPrimary.setOnClickListener { onTransferTap() }
        btnBackfill.setOnClickListener { onBackfillTap() }
        btnSettings.setOnClickListener { onSettingsTap() }

        // Debug-only STEPS/ACTIVITY_SUMMARY deep probe:
        // adb shell am start -n com.zazaki.healthreader/.MainActivity --ez deep true
        if (intent.getBooleanExtra("deep", false)) {
            scope.launch { runDeepProbe() }
            return
        }

        // Debug-only all-DataType probe: adb shell am start -n com.zazaki.healthreader/.MainActivity --ez probe true
        // Skips the normal refresh deliberately (probe-only run); logcat tag "HealthProbe".
        if (intent.getBooleanExtra("probe", false)) {
            scope.launch { runProbe() }
            return
        }

        stateFile = SyncStateStore.load(this)
        today = LocalDate.now()
        windowStart = today
        refreshAll()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    // ================= UI 绑定 =================

    private fun bindViews() {
        llRoot = findViewById(R.id.ll_root)
        tvSubtitle = findViewById(R.id.tv_subtitle)
        tvStatToday = findViewById(R.id.tv_stat_today)
        tvStatConsec = findViewById(R.id.tv_stat_consec)
        tvStatTotal = findViewById(R.id.tv_stat_total)
        llPendingBanner = findViewById(R.id.ll_pending_banner)
        tvPendingBanner = findViewById(R.id.tv_pending_banner)
        btnBackfill = findViewById(R.id.btn_backfill)
        tvDaySectionTitle = findViewById(R.id.tv_day_section_title)
        llDayList = findViewById(R.id.ll_day_list)
        llFeedback = findViewById(R.id.ll_feedback)
        tvFeedback = findViewById(R.id.tv_feedback)
        ivFeedbackCheck = findViewById(R.id.iv_feedback_check)
        btnPrimary = findViewById(R.id.btn_primary)
        btnSettings = findViewById(R.id.btn_settings)
    }

    /** targetSdk 36 强制 edge-to-edge：根视图吸收 systemBars inset，状态栏/导航栏浅色图标。 */
    private fun applyEdgeToEdge() {
        ViewCompat.setOnApplyWindowInsetsListener(llRoot) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = true
            isAppearanceLightNavigationBars = true
        }
    }

    private fun color(res: Int): Int = ContextCompat.getColor(this, res)

    // ================= 主流程 =================

    private fun refreshAll() {
        if (running) return
        scope.launch {
            try {
                withContext(Dispatchers.IO) { scanAndRebuild() }
                renderAll()
                maybeCalendarSync()
                maybeNotifyPending()
                scheduleReminderFromPrefs()
            } catch (t: CancellationException) {
                throw t // 保持结构化取消语义
            } catch (t: Throwable) {
                renderAll() // 尽力展示 store 已有状态，不崩溃
            } finally {
                initialized = true
                updateButtons()
            }
        }
    }

    /**
     * 扫描并重建状态（IO 线程）：
     *  1) 候选窗口 = (today-44)..today（最多 45 行，覆盖规格的 clip 规则）；
     *  2) 逐日解析状态：store 中 sent 永不回退；其余按「SHealth 是否有数据」→ pending / nodata
     *     （SHealth 不可用时沿用 store 旧值，未记录的日视为 nodata）；
     *  3) 收窄窗口：从最老一天起跳过「既无数据又无 sent/pending 记录」的日，
     *     windowStart = 第一个有意义日（有数据或 store 有 sent/pending），全无则只留 today；
     *  4) 重建后持久化 sync_state.json（窗口外仅保留历史 sent，用于连续/总传输统计）。
     */
    private suspend fun scanAndRebuild() {
        val store = ensureStoreAndPerms()
        val now = LocalDate.now()
        today = now

        val oldStates = HashMap(stateFile.states)
        val scanStart = now.minusDays(MAX_ROWS - 1L)

        // 1) 逐日解析候选窗口内的状态（快照 oldStates，避免边扫边改）
        val resolved = HashMap<String, String>()
        var d = scanStart
        while (!d.isAfter(now)) {
            val key = d.toString()
            val old = oldStates[key]
            val state = when {
                old == SyncStateStore.ST_SENT -> SyncStateStore.ST_SENT
                store == null -> old ?: SyncStateStore.ST_NODATA
                else -> if (dayHasData(store, d)) SyncStateStore.ST_PENDING else SyncStateStore.ST_NODATA
            }
            resolved[key] = state
            d = d.plusDays(1)
        }

        // 2) 收窄窗口：最早「有意义」日（有数据或 store 记录为 sent/pending）
        var firstMeaningful = now
        d = scanStart
        while (!d.isAfter(now)) {
            val st = resolved[d.toString()]
            if (st == SyncStateStore.ST_SENT || st == SyncStateStore.ST_PENDING) {
                firstMeaningful = d
                break
            }
            d = d.plusDays(1)
        }
        windowStart = firstMeaningful

        // 3) 重建 store：窗口内 resolved；窗口外仅保留历史 sent
        val newMap = HashMap<String, String>()
        oldStates.forEach { (k, v) ->
            val date = SyncStateStore.parseKey(k)
            if (v == SyncStateStore.ST_SENT &&
                (date == null || date.isBefore(windowStart) || date.isAfter(now))
            ) {
                newMap[k] = v
            }
        }
        d = windowStart
        while (!d.isAfter(now)) {
            newMap[d.toString()] = resolved[d.toString()] ?: SyncStateStore.ST_NODATA
            d = d.plusDays(1)
        }

        stateFile.states = newMap
        stateFile.lastUpdatedAt = LocalDateTime.now().toString()
        SyncStateStore.save(this, stateFile)
        healthStore = store
    }

    // ================= 渲染 =================

    private fun renderAll() {
        updateRows()
        refreshSummaryControls()
        updateSubtitle()
    }

    /** 日期状态表：最新（today）在上，逐行 inflate item_day_row。 */
    private fun updateRows() {
        llDayList.removeAllViews()
        rowViews.clear()
        val count = dayCount()
        if (count <= 0) {
            tvDaySectionTitle.setText(R.string.section_days_default)
            return
        }
        tvDaySectionTitle.text = getString(R.string.section_days_format, count)
        for (i in count - 1 downTo 0) {
            val date = windowStart.plusDays(i.toLong())
            val state = stateFile.states[date.toString()] ?: SyncStateStore.ST_NODATA
            createRow(date, state)
        }
    }

    private fun dayCount(): Int {
        if (windowStart.isAfter(today)) return 0
        return ChronoUnit.DAYS.between(windowStart, today).toInt() + 1
    }

    private fun createRow(date: LocalDate, state: String) {
        val root = LayoutInflater.from(this).inflate(R.layout.item_day_row, llDayList, false)
        val holder = DayRowHolder(
            root.findViewById(R.id.tv_day_date),
            root.findViewById(R.id.tv_day_label),
            root.findViewById(R.id.tv_day_status)
        )
        rowViews[date] = holder
        bindRow(holder, date, state)
        llDayList.addView(root)
    }

    private fun updateRowForDate(date: LocalDate) {
        val state = stateFile.states[date.toString()] ?: SyncStateStore.ST_NODATA
        rowViews[date]?.let { bindRow(it, date, state) }
    }

    /** 行绑定：先设好 text/背景再显示，避免默认灰徽标闪现（designer 注释要求）。 */
    private fun bindRow(h: DayRowHolder, date: LocalDate, state: String) {
        h.tvDate.text = date.format(mdFmt)
        val isToday = date == today
        h.tvLabel.text = if (isToday) getString(R.string.today_marker) else weekdayLabel(date.dayOfWeek)
        h.tvLabel.setTextColor(if (isToday) color(R.color.primary) else color(R.color.text_hint))

        if (isToday && state == SyncStateStore.ST_PENDING) {
            // 今日(待同步)：📤 待同步 / bg_badge_today / primary_pressed
            h.tvStatus.setText(R.string.badge_today)
            h.tvStatus.setBackgroundResource(R.drawable.bg_badge_today)
            h.tvStatus.setTextColor(color(R.color.primary_pressed))
        } else {
            when (state) {
                SyncStateStore.ST_SENT -> {
                    h.tvStatus.setText(R.string.badge_sent)
                    h.tvStatus.setBackgroundResource(R.drawable.bg_badge_sent)
                    h.tvStatus.setTextColor(color(R.color.success_deep))
                }
                SyncStateStore.ST_PENDING -> {
                    h.tvStatus.setText(R.string.badge_pending)
                    h.tvStatus.setBackgroundResource(R.drawable.bg_badge_pending)
                    h.tvStatus.setTextColor(color(R.color.warning_deep))
                }
                else -> {
                    h.tvStatus.setText(R.string.badge_nodata)
                    h.tvStatus.setBackgroundResource(R.drawable.bg_badge_nodata)
                    h.tvStatus.setTextColor(color(R.color.nodata_deep))
                }
            }
        }
    }

    private fun weekdayLabel(dow: DayOfWeek): String = when (dow) {
        DayOfWeek.MONDAY -> getString(R.string.weekday_mon)
        DayOfWeek.TUESDAY -> getString(R.string.weekday_tue)
        DayOfWeek.WEDNESDAY -> getString(R.string.weekday_wed)
        DayOfWeek.THURSDAY -> getString(R.string.weekday_thu)
        DayOfWeek.FRIDAY -> getString(R.string.weekday_fri)
        DayOfWeek.SATURDAY -> getString(R.string.weekday_sat)
        DayOfWeek.SUNDAY -> getString(R.string.weekday_sun)
    }

    /** 统计卡 + 横幅 + 主按钮 + 补传按钮（读 stateFile.states，单数据源）。 */
    private fun refreshSummaryControls() {
        val todayState = stateFile.states[today.toString()] ?: SyncStateStore.ST_NODATA
        val (wordRes, colRes) = when (todayState) {
            SyncStateStore.ST_SENT -> R.string.status_sent_word to R.color.success_deep
            SyncStateStore.ST_PENDING -> R.string.status_today_word to R.color.primary_pressed
            else -> R.string.status_nodata_word to R.color.nodata_deep
        }
        tvStatToday.text = getString(wordRes)
        tvStatToday.setTextColor(color(colRes))

        tvStatConsec.text = getString(R.string.stat_consec_format, consecutiveDays())
        tvStatTotal.text = getString(
            R.string.stat_total_format,
            stateFile.states.values.count { it == SyncStateStore.ST_SENT }
        )

        val pendingN = pendingCount()
        if (pendingN > 0) {
            llPendingBanner.visibility = View.VISIBLE
            tvPendingBanner.text = getString(R.string.pending_banner_format, pendingN)
        } else {
            llPendingBanner.visibility = View.GONE
        }

        val yesterday = today.minusDays(1)
        val yesterdayPending = stateFile.states[yesterday.toString()] == SyncStateStore.ST_PENDING
        btnPrimary.isEnabled = !running && initialized && yesterdayPending
        btnPrimary.text = if (yesterdayPending) {
            getString(R.string.action_transfer_format, yesterday.format(mdFmt))
        } else {
            getString(R.string.action_transfer_default)
        }
        btnBackfill.isEnabled = !running && initialized && pendingN > 0
    }

    private fun updateSubtitle() {
        val last = stateFile.lastSentAt
        val text = last?.let {
            runCatching { LocalDateTime.parse(it) }.getOrNull()
        }?.format(mdhmFmt)?.let {
            getString(R.string.subtitle_last_sync_format, it)
        } ?: getString(R.string.subtitle_default)
        tvSubtitle.text = text
    }

    /** 展示窗口内、早于今天的 pending 数（今日不计入 N）。 */
    private fun pendingCount(): Int {
        var n = 0
        val end = today.minusDays(1)
        var d = windowStart
        while (!d.isAfter(end)) {
            if (stateFile.states[d.toString()] == SyncStateStore.ST_PENDING) n++
            d = d.plusDays(1)
        }
        return n
    }

    private fun pendingDates(): List<LocalDate> {
        val out = ArrayList<LocalDate>()
        val end = today.minusDays(1)
        var d = windowStart
        while (!d.isAfter(end)) {
            if (stateFile.states[d.toString()] == SyncStateStore.ST_PENDING) out.add(d)
            d = d.plusDays(1)
        }
        out.sort()
        return out
    }

    /** 连续已传天数：结束于 today-1（若 today 已传则从 today 起）。 */
    private fun consecutiveDays(): Int {
        var end = today
        if (stateFile.states[today.toString()] != SyncStateStore.ST_SENT) {
            end = today.minusDays(1)
        }
        var streak = 0
        var d = end
        while (streak < 3660 && stateFile.states[d.toString()] == SyncStateStore.ST_SENT) {
            streak++
            d = d.minusDays(1)
        }
        return streak
    }

    // ================= 反馈行 =================

    private fun setFeedback(textRes: Int, colorRes: Int, showCheck: Boolean, autoHide: Boolean) {
        setFeedback(getString(textRes), colorRes, showCheck, autoHide)
    }

    private fun setFeedback(text: CharSequence, colorRes: Int, showCheck: Boolean, autoHide: Boolean) {
        llFeedback.animate().cancel()
        ivFeedbackCheck.animate().cancel()
        llFeedback.visibility = View.VISIBLE
        llFeedback.alpha = 1f
        tvFeedback.text = text
        tvFeedback.setTextColor(color(colorRes))
        if (showCheck) {
            ivFeedbackCheck.visibility = View.VISIBLE
            ivFeedbackCheck.alpha = 0f
            ivFeedbackCheck.scaleX = 0.6f
            ivFeedbackCheck.scaleY = 0.6f
            ivFeedbackCheck.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(250).start()
            llFeedback.animate().setStartDelay(1500L).alpha(0f).setDuration(350).withEndAction {
                llFeedback.visibility = View.GONE
                llFeedback.alpha = 1f
                ivFeedbackCheck.visibility = View.GONE
            }.start()
        } else {
            ivFeedbackCheck.visibility = View.GONE
            if (autoHide) {
                llFeedback.animate().setStartDelay(2200L).alpha(0f).setDuration(300).withEndAction {
                    llFeedback.visibility = View.GONE
                    llFeedback.alpha = 1f
                }.start()
            }
        }
    }

    private fun successFeedback() {
        setFeedback(R.string.feedback_done, R.color.success_deep, showCheck = true, autoHide = true)
    }

    // ================= ① 传输昨日 / ② 补传 =================

    private fun onTransferTap() {
        if (running || !initialized) return
        val date = today.minusDays(1)
        running = true
        updateButtons()
        scope.launch {
            setFeedback(R.string.feedback_reading, R.color.text_secondary, false, false)
            val prep = withContext(Dispatchers.IO) { prepareDayUpload(date) }
            if (prep.file == null) {
                running = false
                updateButtons()
                if (prep.noStore) {
                    setFeedback(R.string.feedback_fail, R.color.error, false, false)
                } else {
                    updateRowForDate(date)
                    refreshSummaryControls()
                    setFeedback(R.string.feedback_empty, R.color.text_secondary, false, true)
                }
                return@launch
            }
            setFeedback(R.string.feedback_progress, R.color.text_secondary, false, false)
            val ok = withContext(Dispatchers.IO) { uploadDayFile(date, prep.file) }
            running = false
            updateButtons()
            if (ok) {
                updateRowForDate(date)
                refreshSummaryControls()
                updateSubtitle()
                successFeedback()
            } else {
                setFeedback(R.string.feedback_fail, R.color.error, false, false)
            }
        }
    }

    private fun onBackfillTap() {
        if (running || !initialized) return
        val targets = pendingDates()
        if (targets.isEmpty()) return
        running = true
        updateButtons()
        scope.launch {
            var failCount = 0
            for (date in targets) {
                setFeedback(R.string.feedback_reading, R.color.text_secondary, false, false)
                val prep = withContext(Dispatchers.IO) { prepareDayUpload(date) }
                if (prep.file == null) {
                    if (prep.noStore) {
                        failCount++
                        break
                    }
                    // 该日数据消失 → 已降级为 nodata
                    updateRowForDate(date)
                    refreshSummaryControls()
                    continue
                }
                setFeedback(R.string.feedback_progress, R.color.text_secondary, false, false)
                val ok = withContext(Dispatchers.IO) { uploadDayFile(date, prep.file) }
                if (ok) {
                    updateRowForDate(date)
                    refreshSummaryControls()
                    updateSubtitle()
                } else {
                    failCount++
                    break // 首个失败停止，剩余保持 pending
                }
            }
            running = false
            updateButtons()
            if (failCount > 0) {
                setFeedback(R.string.feedback_fail, R.color.error, false, false)
            } else if (targets.isNotEmpty()) {
                successFeedback()
            }
        }
    }

    /** IO 线程：导出单日文件；无数据则降级为 nodata。 */
    private suspend fun prepareDayUpload(date: LocalDate): PrepResult {
        return try {
            if (healthStore == null) healthStore = ensureStoreAndPerms()
            val store = healthStore
            if (store == null) return PrepResult(null, true)
            val file = buildDayFile(store, date)
            if (file == null) {
                stateFile.states[date.toString()] = SyncStateStore.ST_NODATA
                stateFile.lastUpdatedAt = LocalDateTime.now().toString()
                SyncStateStore.save(this, stateFile)
                // 该日降级 nodata：立即删除日历里残留的「待同步 yyyy-MM-dd」事件（静默）
                deleteCalendarEventForDate(date)
                PrepResult(null, false)
            } else {
                PrepResult(file, false)
            }
        } catch (t: Throwable) {
            PrepResult(null, true)
        }
    }

    /** IO 线程：上传并标记 sent + 更新日历事件。 */
    private suspend fun uploadDayFile(date: LocalDate, file: File): Boolean {
        return try {
            val code = uploadBytes(file.readBytes(), file.name)
            //noinspection ResultOfMethodCallIgnored
            file.delete()
            if (code in 200..299) {
                stateFile.states[date.toString()] = SyncStateStore.ST_SENT
                stateFile.lastSentAt = LocalDateTime.now().toString()
                stateFile.lastUpdatedAt = LocalDateTime.now().toString()
                SyncStateStore.save(this, stateFile)
                syncCalendarForDate(date, done = true)
                true
            } else {
                false
            }
        } catch (t: Throwable) {
            false
        }
    }

    private fun updateButtons() {
        val pendingN = pendingCount()
        val yesterdayPending = stateFile.states[today.minusDays(1).toString()] == SyncStateStore.ST_PENDING
        btnPrimary.isEnabled = !running && initialized && yesterdayPending
        btnBackfill.isEnabled = !running && initialized && pendingN > 0
    }

    // ================= ④ 日历联动 =================

    private suspend fun maybeCalendarSync() {
        if (stateFile.states.isEmpty()) return
        if (CalendarSync.getCalId(this) != null && CalendarSync.isPermGranted(this)) {
            syncAllCalendarEvents()
            return
        }
        val granted = requestCalendarPermission()
        if (granted) {
            syncAllCalendarEvents()
        } else {
            setFeedback(R.string.feedback_calendar_permission, R.color.error, false, true)
        }
    }

    /**
     * 为当前 store 状态同步日历：
     *  - 清理：中立化本日历下「标题日期不在 activeIso（sent/pending 日期集）」的事件，
     *    覆盖窗口之外的历史残留（如已降级 nodata 的 09-05 旧「待同步」事件）；
     *  - upsert：窗口内 sent → ✅ 已同步 / pending → 待同步（顺带修正错位日期）。
     */
    private suspend fun syncAllCalendarEvents() {
        withContext(Dispatchers.IO) {
            try {
                val calId = CalendarSync.ensureCalendar(this@MainActivity, getString(R.string.app_name))
                    ?: return@withContext
                // 当前有效日期集合：store 中所有 sent / pending 的日期（ISO 键），含窗口外历史 sent
                val activeIso = HashSet<String>()
                stateFile.states.forEach { (k, v) ->
                    if (v == SyncStateStore.ST_SENT || v == SyncStateStore.ST_PENDING) activeIso.add(k)
                }
                // 中立化标题日期不在 activeIso 中的事件（无论是否在展示窗口内）
                CalendarSync.neutralizeEventsNotActive(this@MainActivity, calId, activeIso)
                // upsert 窗口内 sent/pending 日（nodata 日已由上面的清理覆盖）
                var d = windowStart
                val end = today
                while (!d.isAfter(end)) {
                    val st = stateFile.states[d.toString()]
                    when (st) {
                        SyncStateStore.ST_SENT ->
                            CalendarSync.upsertDayEvent(this@MainActivity, calId, d, done = true)
                        SyncStateStore.ST_PENDING ->
                            CalendarSync.upsertDayEvent(this@MainActivity, calId, d, done = false)
                        else -> Unit // nodata：不建事件；历史残留由 neutralizeEventsNotActive 清理
                    }
                    d = d.plusDays(1)
                }
            } catch (t: Throwable) {
                // 日历不可用不影响主功能
            }
        }
    }

    /** 单日传输成功后把事件更新为 ✅ 已同步（调用方在 IO 线程）。 */
    private fun syncCalendarForDate(date: LocalDate, done: Boolean) {
        try {
            val calId = CalendarSync.getCalId(this) ?: return
            if (!CalendarSync.isPermGranted(this)) return
            CalendarSync.upsertDayEvent(this, calId, date, done)
        } catch (t: Throwable) {
            // 静默
        }
    }

    /** 单日降级 nodata 后删除该日日历事件（调用方在 IO 线程；静默失败）。 */
    private fun deleteCalendarEventForDate(date: LocalDate) {
        try {
            val calId = CalendarSync.getCalId(this) ?: return
            if (!CalendarSync.isPermGranted(this)) return
            CalendarSync.deleteDayEvent(this, calId, date)
        } catch (t: Throwable) {
            // 静默
        }
    }

    private suspend fun requestCalendarPermission(): Boolean {
        if (CalendarSync.isPermGranted(this)) return true
        if (calendarPermCont != null) return false
        return suspendCancellableCoroutine { cont ->
            calendarPermCont = cont
            calendarPermLauncher.launch(
                arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR)
            )
            cont.invokeOnCancellation { calendarPermCont = null }
        }
    }

    // ================= ⑤ 提醒 =================

    /** 打开时通知：N>0 且与上次不同才发一条；N==0 重置去重值。 */
    private suspend fun maybeNotifyPending() {
        val n = pendingCount()
        PendingNotifier.ensureChannel(this)
        if (n > 0 && !PendingNotifier.canNotify(this)) {
            val granted = requestNotificationPermission()
            if (!granted) return
        }
        val prefs = getSharedPreferences(PREFS_APP, Context.MODE_PRIVATE)
        val last = prefs.getInt(KEY_LAST_NOTIFIED_N, -1)
        if (n == 0) {
            if (last != 0) prefs.edit().putInt(KEY_LAST_NOTIFIED_N, 0).apply()
            return
        }
        if (n != last) {
            PendingNotifier.post(this, n)
            prefs.edit().putInt(KEY_LAST_NOTIFIED_N, n).apply()
        }
    }

    private suspend fun requestNotificationPermission(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        if (PendingNotifier.canNotify(this)) return true
        if (notifPermCont != null) return false
        return suspendCancellableCoroutine { cont ->
            notifPermCont = cont
            notifPermLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            cont.invokeOnCancellation { notifPermCont = null }
        }
    }

    private fun onSettingsTap() {
        val prefs = getSharedPreferences(ReminderScheduler.PREFS, Context.MODE_PRIVATE)
        var hour = 20
        var minute = 0
        prefs.getString(ReminderScheduler.KEY_REMINDER_HHMM, null)?.let { hhmm ->
            runCatching {
                val parts = hhmm.split(":")
                if (parts.size == 2) {
                    hour = parts[0].toInt()
                    minute = parts[1].toInt()
                }
            }
        }
        val dialog = TimePickerDialog(
            this,
            TimePickerDialog.OnTimeSetListener { _, h, m -> onReminderChosen(h, m) },
            hour,
            minute,
            true
        )
        dialog.setTitle(getString(R.string.reminder_time_label))
        dialog.show()
    }

    private fun onReminderChosen(hour: Int, minute: Int) {
        val hhmm = String.format(Locale.US, "%02d:%02d", hour, minute)
        getSharedPreferences(ReminderScheduler.PREFS, Context.MODE_PRIVATE)
            .edit().putString(ReminderScheduler.KEY_REMINDER_HHMM, hhmm).apply()
        ReminderScheduler.scheduleNext(this)
        setFeedback(getString(R.string.feedback_reminder_set, hhmm), R.color.text_secondary, false, true)
    }

    /** 每次打开时按偏好时间重排下一次一次性闹钟（未设置则静默无操作）。 */
    private fun scheduleReminderFromPrefs() {
        ReminderScheduler.scheduleNext(this)
    }

    // ================= SHealth 读取（沿用 v0.2 验证过的反射辅助） =================

    private fun log(s: String) {
        // v0.2 的日志视图已移除；保留空实现以便反射辅助原样复用
    }

    private suspend fun ensureStoreAndPerms(): HealthDataStore? {
        val store = try {
            HealthDataService.getStore(applicationContext)
        } catch (e: Throwable) {
            log("getStore FAILED: ${e.javaClass.simpleName}: ${e.message}")
            return null
        }
        val want = setOf(
            Permission.of(DataTypes.HEART_RATE, AccessType.READ),
            Permission.of(DataTypes.SLEEP, AccessType.READ),
            Permission.of(DataTypes.BLOOD_OXYGEN, AccessType.READ),
            Permission.of(DataTypes.SKIN_TEMPERATURE, AccessType.READ),
            Permission.of(DataTypes.ENERGY_SCORE, AccessType.READ),
            Permission.of(DataTypes.EXERCISE, AccessType.READ),
            Permission.of(DataTypes.WATER_INTAKE, AccessType.READ),
            Permission.of(DataTypes.BODY_COMPOSITION, AccessType.READ),
            Permission.of(DataTypes.STEPS, AccessType.READ),
            Permission.of(DataTypes.ACTIVITY_SUMMARY, AccessType.READ),
            Permission.of(DataTypes.FLOORS_CLIMBED, AccessType.READ),
            Permission.of(DataTypes.BLOOD_PRESSURE, AccessType.READ),
            Permission.of(DataTypes.BLOOD_GLUCOSE, AccessType.READ),
            Permission.of(DataTypes.BODY_TEMPERATURE, AccessType.READ),
            Permission.of(DataTypes.NUTRITION, AccessType.READ)
        )
        // 1) 先查询已授权集合（suspend、无 UI）——冷启动/日常启动稳态路径：已全授则静默返回，不再弹 SHealth 授权框。
        val already = try {
            store.getGrantedPermissions(want)
        } catch (e: Throwable) {
            log("getGrantedPermissions: ${e.javaClass.simpleName}: ${e.message}")
            null
        }
        if (already != null && already.containsAll(want)) {
            log("perms already granted (${already.size}/${want.size})")
            return store
        }
        // 2) 仅当存在缺失权限时才走 UI 授权流程（首次运行 / 重装后 / 用户在系统设置中撤销授权）
        return try {
            val granted = store.requestPermissions(want, this)
            log("granted ${granted.size}/${want.size}")
            if (granted.containsAll(want)) store else null
        } catch (e: Throwable) {
            log("requestPermissions: ${e.javaClass.simpleName}: ${e.message}")
            store // best-effort
        }
    }

    /** 单日提取结果：原始 dp 列表 + 可写计数（扫描与导出共用的唯一判定来源）。 */
    private class DayExtract(
        val hrDps: List<HealthDataPoint>,
        val hrCount: Int,
        val sleepDps: List<HealthDataPoint>,
        val sleepCount: Int
    )

    /**
     * 单日统一提取器：扫描（dayHasData）与导出（buildDayFile）共用同一套计数规则，
     * 保证「扫描认为有数据」⇔「导出能写出内容」，杜绝 09-05 shell-dp 的 pending/nodata 振荡。
     * HR 计数 = SERIES_DATA 去重样本数 + 标量 HEART_RATE 数（与导出写循环完全一致）；
     * SLEEP 计数 = dpStart 非空的 dp 数（与导出写循环一致）。
     */
    private suspend fun readDayExtract(store: HealthDataStore, date: LocalDate): DayExtract {
        val start = date.atStartOfDay()
        val end = date.plusDays(1).atStartOfDay()
        return try {
            val hrDps = readDayPoints(store, DataTypes.HEART_RATE, start, end)
            var hrCount = 0
            val seen = HashSet<String>()
            for (dp in hrDps) {
                val series = getField<List<HeartRate>>(DataTypes.HEART_RATE, "SERIES_DATA", dp)
                if (series != null) {
                    for (s in series) {
                        if (seen.add(s.startTime.toString())) hrCount++
                    }
                } else {
                    val hr = getField<Number>(DataTypes.HEART_RATE, "HEART_RATE", dp)
                    val st = dpStart(dp)
                    if (hr != null && st != null && seen.add(st.toString())) hrCount++
                }
            }
            if (hrDps.isNotEmpty() && hrCount == 0) {
                // 防御性日志：验证 shell-dp 假设（dp 存在但无可读内容）——设备运行后可 grep logcat
                val detail = hrDps.joinToString("; ") { dp ->
                    val s = getField<List<HeartRate>>(DataTypes.HEART_RATE, "SERIES_DATA", dp)
                    "start=${dpStart(dp)} series=${if (s == null) "null" else if (s.isEmpty()) "empty" else "size=${s.size}"}"
                }
                android.util.Log.w("HealthReader", "shell HR dp: date=$date dps=${hrDps.size} [$detail]")
            }
            val sleepDps = readDayPoints(store, DataTypes.SLEEP, start, end)
            var sleepCount = 0
            for (dp in sleepDps) {
                if (dpStart(dp) != null) sleepCount++
            }
            DayExtract(hrDps, hrCount, sleepDps, sleepCount)
        } catch (t: Throwable) {
            // 与旧 dayHasData 相同的韧性：异常日按 nodata 处理
            DayExtract(emptyList(), 0, emptyList(), 0)
        }
    }

    /** 判断本地日是否「有数据」：统一走 readDayExtract 的可写计数（>0 才算有数据）。 */
    private suspend fun dayHasData(store: HealthDataStore, date: LocalDate): Boolean {
        val e = readDayExtract(store, date)
        return e.hrCount > 0 || e.sleepCount > 0
    }

    // ================= 单日导出 + 上传（沿用 v0.2 流式 JsonWriter） =================

    /** 导出单日 YYYYMMDD_sleep_hr.json；该日无可写数据（hrCount+sleepCount==0）时返回 null（不落文件）。 */
    private suspend fun buildDayFile(store: HealthDataStore, date: LocalDate): File? {
        // 与扫描共用 readDayExtract：可写内容判定与 dayHasData 永不分歧
        val extract = readDayExtract(store, date)
        if (extract.hrCount == 0 && extract.sleepCount == 0) return null
        val start = date.atStartOfDay()
        val end = date.plusDays(1).atStartOfDay()
        val tz = ZoneId.systemDefault()
        val dir = File(filesDir, "exports").apply { mkdirs() }
        val fileName = date.format(DateTimeFormatter.BASIC_ISO_DATE) + "_sleep_hr.json"
        val f = File(dir, fileName)
        var completed = false
        try {
            val jw = com.google.gson.stream.JsonWriter(
                java.io.BufferedWriter(java.io.OutputStreamWriter(f.outputStream(), Charsets.UTF_8))
            )
            jw.setIndent("  ")
            try {
                jw.beginObject()
                jw.name("exported_at").value(Instant.now().toString())
                jw.name("tz").value(tz.id)
                jw.name("days").value(1)

                jw.name("heart_rate")
                jw.beginArray()
                val seenHr = HashSet<String>()
                for (dp in extract.hrDps) {
                    val series = getField<List<HeartRate>>(DataTypes.HEART_RATE, "SERIES_DATA", dp)
                    if (series != null) {
                        for (s in series) {
                            if (seenHr.add(s.startTime.toString())) {
                                jw.beginObject()
                                jw.name("t").value(s.startTime.toString())
                                jw.name("hr").value(s.heartRate.toDouble())
                                jw.endObject()
                            }
                        }
                    } else {
                        val hr = getField<Number>(DataTypes.HEART_RATE, "HEART_RATE", dp)
                        val st = dpStart(dp)
                        if (hr != null && st != null && seenHr.add(st.toString())) {
                            jw.beginObject()
                            jw.name("t").value(st.toString())
                            jw.name("hr").value(hr.toFloat().toDouble())
                            jw.endObject()
                        }
                    }
                }
                jw.endArray()

                jw.name("sleep")
                jw.beginArray()
                for (dp in extract.sleepDps) {
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
                }
                jw.endArray()

                writeOxygenArray(jw, store, start, end)
                writeSkinTempArray(jw, store, start, end)
                writeEnergyArray(jw, store, start, end)
                writeExerciseArray(jw, store, start, end)
                writeWaterArray(jw, store, start, end)
                writeBodyCompArray(jw, store, start, end)

                writeArray(jw, "steps") { readStepsRows(store, start, end) }
                writeArray(jw, "activity") { readActivityRows(store, start, end) }
                writeArray(jw, "floors") { readFloorsRows(store, start, end) }
                writeArray(jw, "blood_pressure") { readBloodPressureRows(store, start, end) }
                writeArray(jw, "blood_glucose") { readBloodGlucoseRows(store, start, end) }
                writeArray(jw, "body_temperature") { readBodyTempRows(store, start, end) }
                writeArray(jw, "nutrition") { readNutritionRows(store, start, end) }

                jw.endObject()
                completed = true
            } finally {
                try { jw.close() } catch (t: Throwable) {}
            }
        } catch (t: Throwable) {
            //noinspection ResultOfMethodCallIgnored
            f.delete()
            return null
        }
        if (!completed) {
            //noinspection ResultOfMethodCallIgnored
            f.delete()
            return null
        }
        return f
    }

    // ================= v3 多类型导出辅助（HEALTH_EXTEND_V3_SPEC，schema 与 analyzer 对齐） =================

    private fun enumName(v: Any?): String? = when (v) {
        is Enum<*> -> v.name
        else -> v?.toString()
    }

    private fun jwMap(jw: com.google.gson.stream.JsonWriter, m: Map<String, Any?>) {
        jw.beginObject()
        for ((k, v) in m) {
            if (v == null) continue
            jw.name(k)
            when (v) {
                is Number -> jw.value(v.toDouble())
                is Boolean -> jw.value(v)
                else -> jw.value(v.toString())
            }
        }
        jw.endObject()
    }

    private suspend fun readOxygenRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.BLOOD_OXYGEN, start, end)
        for (dp in dps) {
            try {
                val series = getField<List<OxygenSaturation>>(DataTypes.BLOOD_OXYGEN, "SERIES_DATA", dp)
                if (series != null && series.isNotEmpty()) {
                    for (s in series) {
                        rows.add(linkedMapOf("t" to s.startTime.toString(),
                                             "spo2" to s.oxygenSaturation.toDouble()))
                    }
                } else {
                    val st = dpStart(dp)
                    val spo2 = getField<Number>(DataTypes.BLOOD_OXYGEN, "OXYGEN_SATURATION", dp)
                    if (st != null && spo2 != null) {
                        rows.add(linkedMapOf(
                            "t" to st.toString(),
                            "spo2" to spo2.toDouble(),
                            "min" to getField<Number>(DataTypes.BLOOD_OXYGEN, "MIN_OXYGEN_SATURATION", dp)?.toDouble(),
                            "max" to getField<Number>(DataTypes.BLOOD_OXYGEN, "MAX_OXYGEN_SATURATION", dp)?.toDouble()
                        ))
                    }
                }
            } catch (t: Throwable) {
                log("v3 oxygen row skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun readSkinTempRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.SKIN_TEMPERATURE, start, end)
        for (dp in dps) {
            try {
                val series = getField<List<SkinTemperature>>(DataTypes.SKIN_TEMPERATURE, "SERIES_DATA", dp)
                if (series != null && series.isNotEmpty()) {
                    for (s in series) {
                        rows.add(linkedMapOf("t" to s.startTime.toString(),
                                             "temp" to s.skinTemperature.toDouble()))
                    }
                } else {
                    val st = dpStart(dp)
                    val temp = getField<Number>(DataTypes.SKIN_TEMPERATURE, "SKIN_TEMPERATURE", dp)
                    if (st != null && temp != null) {
                        rows.add(linkedMapOf(
                            "t" to st.toString(),
                            "temp" to temp.toDouble(),
                            "min" to getField<Number>(DataTypes.SKIN_TEMPERATURE, "MIN_SKIN_TEMPERATURE", dp)?.toDouble(),
                            "max" to getField<Number>(DataTypes.SKIN_TEMPERATURE, "MAX_SKIN_TEMPERATURE", dp)?.toDouble()
                        ))
                    }
                }
            } catch (t: Throwable) {
                log("v3 skin skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun readEnergyRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.ENERGY_SCORE, start, end)
        for (dp in dps) {
            try {
                val st = dpStart(dp)
                val score = getField<Number>(DataTypes.ENERGY_SCORE, "ENERGY_SCORE", dp)
                if (st != null && score != null) {
                    rows.add(linkedMapOf("t" to st.toString(), "score" to score.toDouble()))
                }
            } catch (t: Throwable) {
                log("v3 energy skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun readExerciseRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.EXERCISE, start, end)
        for (dp in dps) {
            try {
                val dpType = enumName(getField<Any>(DataTypes.EXERCISE, "EXERCISE_TYPE", dp))
                val dpTitle = getField<String>(DataTypes.EXERCISE, "CUSTOM_TITLE", dp)
                val sessions = getField<List<ExerciseSession>>(DataTypes.EXERCISE, "SESSIONS", dp)
                var emitted = false
                for (sess in sessions ?: emptyList()) {
                    val st = sess.startTime ?: continue
                    val en = sess.endTime
                    val typeName = enumName(sess.exerciseType) ?: dpType
                    val title = sess.customTitle ?: dpTitle
                    val durS = if (en != null && en.isAfter(st))
                        java.time.temporal.ChronoUnit.SECONDS.between(st, en) else null
                    val cal = sess.calories
                    val row = linkedMapOf<String, Any?>(
                        "t" to st.toString(),
                        "type" to (typeName ?: "UNKNOWN"),
                        "title" to title
                    )
                    if (durS != null) row["duration_s"] = durS.toDouble()
                    if (cal != null && cal > 0f) row["calories"] = cal.toDouble()
                    rows.add(row)
                    emitted = true
                }
                if (!emitted) {
                    val st = dpStart(dp) ?: continue
                    rows.add(linkedMapOf(
                        "t" to st.toString(),
                        "type" to (dpType ?: "UNKNOWN"),
                        "title" to dpTitle
                    ))
                }
            } catch (t: Throwable) {
                log("v3 exercise skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun readWaterRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.WATER_INTAKE, start, end)
        for (dp in dps) {
            try {
                val st = dpStart(dp)
                val amount = getField<Number>(DataTypes.WATER_INTAKE, "AMOUNT", dp)
                if (st != null && amount != null && amount.toDouble() > 0) {
                    rows.add(linkedMapOf("t" to st.toString(), "amount" to amount.toDouble()))
                }
            } catch (t: Throwable) {
                log("v3 water skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun readBodyCompRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.BODY_COMPOSITION, start, end)
        val numKeys = listOf(
            "WEIGHT" to "weight", "HEIGHT" to "height", "BODY_FAT" to "body_fat",
            "SKELETAL_MUSCLE" to "skeletal_muscle", "MUSCLE_MASS" to "muscle_mass",
            "TOTAL_BODY_WATER" to "total_body_water")
        for (dp in dps) {
            try {
                val st = dpStart(dp) ?: continue
                val row = linkedMapOf<String, Any?>("t" to st.toString())
                for ((fieldName, key) in numKeys) {
                    val v = getField<Number>(DataTypes.BODY_COMPOSITION, fieldName, dp)
                    if (v != null && v.toDouble() > 0) row[key] = v.toDouble()
                }
                val bmr = getField<Number>(DataTypes.BODY_COMPOSITION, "BASAL_METABOLIC_RATE", dp)
                if (bmr != null && bmr.toDouble() > 0) row["basal_metabolic_rate"] = bmr.toDouble()
                if (row.size > 1) rows.add(row)
            } catch (t: Throwable) {
                log("v3 bodycomp skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun writeArray(
        jw: com.google.gson.stream.JsonWriter, name: String,
        rows: suspend () -> List<Map<String, Any?>>
    ) {
        jw.name(name)
        jw.beginArray()
        try {
            for (r in rows()) jwMap(jw, r)
        } catch (t: Throwable) {
            log("v3 $name write err ${t.javaClass.simpleName}: ${t.message}")
        }
        jw.endArray()
    }

    private suspend fun writeOxygenArray(
        jw: com.google.gson.stream.JsonWriter, store: HealthDataStore,
        start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ) = writeArray(jw, "blood_oxygen") { readOxygenRows(store, start, end) }

    private suspend fun writeSkinTempArray(
        jw: com.google.gson.stream.JsonWriter, store: HealthDataStore,
        start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ) = writeArray(jw, "skin_temperature") { readSkinTempRows(store, start, end) }

    private suspend fun writeEnergyArray(
        jw: com.google.gson.stream.JsonWriter, store: HealthDataStore,
        start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ) = writeArray(jw, "energy_score") { readEnergyRows(store, start, end) }

    private suspend fun writeExerciseArray(
        jw: com.google.gson.stream.JsonWriter, store: HealthDataStore,
        start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ) = writeArray(jw, "exercise") { readExerciseRows(store, start, end) }

    private suspend fun writeWaterArray(
        jw: com.google.gson.stream.JsonWriter, store: HealthDataStore,
        start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ) = writeArray(jw, "water_intake") { readWaterRows(store, start, end) }

    private suspend fun writeBodyCompArray(
        jw: com.google.gson.stream.JsonWriter, store: HealthDataStore,
        start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ) = writeArray(jw, "body_composition") { readBodyCompRows(store, start, end) }

    // ================= v5 全量接入辅助（HEALTH_EXTEND_V5_SPEC） =================

    private fun localDayStartIso(start: java.time.LocalDateTime): String =
        start.atZone(java.time.ZoneId.systemDefault()).toInstant().toString()

    /** 单日窗口聚合值：反射取 type class 上 static op 字段 -> builder -> 请求 -> dataList 首条 value。 */
    @Suppress("UNCHECKED_CAST")
    private suspend fun aggregateDayValue(
        store: HealthDataStore, type: com.samsung.android.sdk.health.data.request.DataType,
        opField: String, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): Any? {
        return try {
            val opFieldRef = type.javaClass.getField(opField)
            val op = opFieldRef.get(null) ?: return null          // AggregateOperation<T, Builder<T>> (static)
            val builder = op.javaClass.getMethod("getRequestBuilder").invoke(op)
            val bc = builder.javaClass
            try {
                bc.getMethod("setLocalTimeFilter", LocalTimeFilter::class.java)
                    .invoke(builder, LocalTimeFilter.of(start, end))
            } catch (t: Throwable) {
                log("v5 agg filter $opField ${t.javaClass.simpleName}: ${t.message}")
            }
            val req = bc.getMethod("build").invoke(builder) as AggregateRequest<Any>
            val resp = store.aggregateData(req)
            val list = resp.dataList as? List<AggregatedData<Any>> ?: emptyList()
            list.firstOrNull()?.value
        } catch (t: Throwable) {
            log("v5 agg $opField ${t.javaClass.simpleName}: ${t.message}")
            null
        }
    }

    private suspend fun readStepsRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val v = aggregateDayValue(store, DataTypes.STEPS, "TOTAL", start, end)
        val n = (v as? Number)?.toLong()
        return if (n != null && n > 0)
            listOf(linkedMapOf("t" to localDayStartIso(start), "count" to n)) else emptyList()
    }

    private suspend fun readActivityRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val dur = aggregateDayValue(store, DataTypes.ACTIVITY_SUMMARY, "TOTAL_ACTIVE_TIME", start, end)
        val row = linkedMapOf<String, Any?>("t" to localDayStartIso(start))
        val actS = (dur as? java.time.Duration)?.seconds
            ?: (dur as? Number)?.toLong()
        if (actS != null && actS > 0) row["active_time_s"] = actS
        val cal = aggregateDayValue(store, DataTypes.ACTIVITY_SUMMARY, "TOTAL_ACTIVE_CALORIES_BURNED", start, end)
        if ((cal as? Number)?.toDouble()?.takeIf { it > 0 } != null) row["active_calories"] = (cal as Number).toDouble()
        val tcb = aggregateDayValue(store, DataTypes.ACTIVITY_SUMMARY, "TOTAL_CALORIES_BURNED", start, end)
        if ((tcb as? Number)?.toDouble()?.takeIf { it > 0 } != null) row["total_calories_burned"] = (tcb as Number).toDouble()
        val dist = aggregateDayValue(store, DataTypes.ACTIVITY_SUMMARY, "TOTAL_DISTANCE", start, end)
        if ((dist as? Number)?.toDouble()?.takeIf { it > 0 } != null) row["distance_m"] = (dist as Number).toDouble()
        return if (row.size > 1) listOf(row) else emptyList()
    }

    private suspend fun readFloorsRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val v = aggregateDayValue(store, DataTypes.FLOORS_CLIMBED, "TOTAL", start, end)
        val n = (v as? Number)?.toDouble()
        return if (n != null && n > 0)
            listOf(linkedMapOf("t" to localDayStartIso(start), "count" to n)) else emptyList()
    }

    private suspend fun readBloodPressureRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.BLOOD_PRESSURE, start, end)
        for (dp in dps) {
            try {
                val st = dpStart(dp) ?: continue
                val sys = getField<Number>(DataTypes.BLOOD_PRESSURE, "SYSTOLIC", dp)?.toDouble()
                val dia = getField<Number>(DataTypes.BLOOD_PRESSURE, "DIASTOLIC", dp)?.toDouble()
                val pulse = getField<Number>(DataTypes.BLOOD_PRESSURE, "PULSE_RATE", dp)?.toDouble()
                if (sys == null && dia == null && pulse == null) continue
                rows.add(linkedMapOf("t" to st.toString(), "systolic" to sys,
                                     "diastolic" to dia, "pulse" to pulse))
            } catch (t: Throwable) {
                log("v5 bp skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun readBloodGlucoseRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.BLOOD_GLUCOSE, start, end)
        for (dp in dps) {
            try {
                val st = dpStart(dp) ?: continue
                val gl = getField<Number>(DataTypes.BLOOD_GLUCOSE, "GLUCOSE_LEVEL", dp)?.toDouble()
                if (gl == null) continue
                val mt = enumName(getField<Any>(DataTypes.BLOOD_GLUCOSE, "MEASUREMENT_TYPE", dp))
                rows.add(linkedMapOf("t" to st.toString(), "glucose" to gl,
                                     "measurement_type" to mt))
            } catch (t: Throwable) {
                log("v5 glucose skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun readBodyTempRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.BODY_TEMPERATURE, start, end)
        for (dp in dps) {
            try {
                val st = dpStart(dp) ?: continue
                val t = getField<Number>(DataTypes.BODY_TEMPERATURE, "BODY_TEMPERATURE", dp)?.toDouble()
                if (t != null) rows.add(linkedMapOf("t" to st.toString(), "temp" to t))
            } catch (t: Throwable) {
                log("v5 bodytemp skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    private suspend fun readNutritionRows(
        store: HealthDataStore, start: java.time.LocalDateTime, end: java.time.LocalDateTime
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val dps = readDayPoints(store, DataTypes.NUTRITION, start, end)
        for (dp in dps) {
            try {
                val st = dpStart(dp) ?: continue
                val title = getField<String>(DataTypes.NUTRITION, "TITLE", dp)
                val mt = enumName(getField<Any>(DataTypes.NUTRITION, "MEAL_TYPE", dp))
                val cal = getField<Number>(DataTypes.NUTRITION, "CALORIES", dp)?.toDouble()
                val carbs = getField<Number>(DataTypes.NUTRITION, "CARBOHYDRATE", dp)?.toDouble()
                val protein = getField<Number>(DataTypes.NUTRITION, "PROTEIN", dp)?.toDouble()
                val fat = getField<Number>(DataTypes.NUTRITION, "TOTAL_FAT", dp)?.toDouble()
                if (title == null && cal == null && carbs == null && protein == null && fat == null) continue
                rows.add(linkedMapOf(
                    "t" to st.toString(),
                    "title" to (if (title != null && title.isNotBlank()) title else null),
                    "meal_type" to mt,
                    "calories" to cal, "carbs" to carbs, "protein" to protein, "fat" to fat))
            } catch (t: Throwable) {
                log("v5 nutrition skip ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return rows
    }

    /**
     * POST 原始 JSON 字节到电脑接收端，返回 HTTP code（异常 / 非 2xx 由调用方判定）。
     * 文件名拼入 URL 路径末段（/upload/<YYYYMMDD_sleep_hr.json>），接收端据此幂等落盘（同日覆盖）。
     */
    private suspend fun uploadBytes(body: ByteArray, fileName: String): Int {
        var code = -1
        try {
            // 防御性净化：仅保留 URL 路径安全字符（文件名本身是 YYYYMMDD_sleep_hr.json，安全集内）
            val safeName = fileName.replace(Regex("[^A-Za-z0-9_.-]"), "_")
            val conn = java.net.URL("$UPLOAD_URL/$safeName").openConnection() as java.net.HttpURLConnection
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 10000
            conn.readTimeout = 30000
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Content-Length", body.size.toString())
            conn.outputStream.use { it.write(body) }
            code = conn.responseCode
            try {
                if (code in 200..299) {
                    conn.inputStream.bufferedReader().readText()
                } else {
                    conn.errorStream?.bufferedReader()?.readText() ?: ""
                }
            } catch (_: Throwable) {}
            conn.disconnect()
        } catch (t: Throwable) {
            code = -1
        }
        return code
    }

    // ================= v0.2 原样反射辅助 =================

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

    // ================= Debug-only: all-DataType probe (intent extra "probe") =================
    // Triggered ONLY by `--ez probe true` on the launcher intent. Runs entirely in logcat under
    // tag "HealthProbe": enumerates every DataType exported by the SDK, classifies READ grant
    // state, requests all missing permissions in ONE call, then counts data presence over the
    // last 14 local days per granted type. Reuses readDayPoints / dpStart unchanged.

    private suspend fun runProbe() {
        val named = enumerateProbeTypes()
        try {
            withContext(Dispatchers.IO) { probeAll(named) }
        } catch (t: CancellationException) {
            throw t // 保持结构化取消语义
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_ERR runProbe ${t.javaClass.simpleName}: ${t.message}")
        } finally {
            // 终止标记保证必达：即使个别类型失败或 store 不可用，logcat 也能判定 probe 结束
            android.util.Log.i("HealthProbe", "PROBE_DONE total=${named.size}")
        }
    }

    /** (field name, DataType) pairs for every DataType-typed public field of the SDK interface, sorted by field name. */
    private fun enumerateProbeTypes(): List<Pair<String, DataType>> {
        return try {
            DataTypes::class.java.fields
                .asSequence()
                .filter { DataType::class.java.isAssignableFrom(it.type) }
                .mapNotNull { f ->
                    try {
                        f.name to (f.get(null) as DataType)
                    } catch (t: Throwable) {
                        android.util.Log.i("HealthProbe", "PROBE_ERR field ${f.name} ${t.javaClass.simpleName}: ${t.message}")
                        null
                    }
                }
                .sortedBy { it.first }
                .toList()
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_ERR enum ${t.javaClass.simpleName}: ${t.message}")
            emptyList()
        }
    }

    /**
     * 分类 → 一次性请求缺失权限 → 每类型「重查 + 14 天扫描 + 汇总」。
     * 隔离策略：单个病态类型任何环节抛异常（如 USER_PROFILE 返回 UserDataPoint 引发的
     * ClassCastException）只会让该类型自己的行降级为 0 并附 err= 后缀，绝不中断整个 probe。
     */
    private suspend fun probeAll(named: List<Pair<String, DataType>>) {
        val store = try {
            HealthDataService.getStore(applicationContext)
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_STORE_FAIL ${t.javaClass.simpleName}: ${t.message}")
            return
        }

        // 1) 逐类型分类授权状态（每类型独立 try 隔离）；missing 汇总后走下方单次请求
        val missing = LinkedHashMap<String, Permission>() // 字段名 -> READ 权限（待一次性请求）
        val knownGranted = LinkedHashSet<String>()        // 已确认 granted=true 的类型
        val failedClassify = LinkedHashMap<String, String>() // 分类阶段异常的类型 -> err 详情
        for ((name, dt) in named) {
            try {
                val perm = try {
                    Permission.of(dt, AccessType.READ)
                } catch (t: Throwable) {
                    // 类型不参与权限门控（Permission.of 不可构造）→ granted=false，不请求
                    null
                }
                if (perm == null) continue
                val grantedNow = try {
                    store.getGrantedPermissions(setOf(perm)).contains(perm)
                } catch (t: Throwable) {
                    // getGrantedPermissions 抛异常 → 类型不参与权限门控 / 不受支持 → 视为未授权，仍尝试请求
                    android.util.Log.i("HealthProbe", "PROBE_ERR grant $name ${t.javaClass.simpleName}: ${t.message}")
                    false
                }
                if (grantedNow) knownGranted.add(name) else missing[name] = perm
            } catch (t: CancellationException) {
                throw t // 保持结构化取消语义
            } catch (t: Throwable) {
                failedClassify[name] = "${t.javaClass.simpleName}: ${t.message}"
                android.util.Log.i("HealthProbe", "PROBE_ERR classify $name ${t.javaClass.simpleName}: ${t.message}")
            }
        }

        // 2) 一次性请求所有缺失权限（部分可能被系统拒绝 / 不可请求；吸收任何异常）
        if (missing.isNotEmpty()) {
            try {
                store.requestPermissions(missing.values.toSet(), this@MainActivity)
            } catch (t: Throwable) {
                android.util.Log.i("HealthProbe", "PROBE_ERR request ${t.javaClass.simpleName}: ${t.message}")
            }
        }

        // 3) 每类型：重查 + 14 天扫描 + 汇总 —— 整段独立 try，病态类型只能伤到自己的行
        val today = LocalDate.now()
        for ((name, dt) in named) {
            var grantedKnown = false // catch 兜底时输出「失败前已知」的授权状态
            try {
                val classifyErr = failedClassify[name]
                if (classifyErr != null) {
                    android.util.Log.i("HealthProbe", "PROBE_RESULT $name granted=false days_with_data=0 dps=0 range=- err=$classifyErr")
                    continue
                }
                val perm = try {
                    Permission.of(dt, AccessType.READ)
                } catch (t: Throwable) {
                    android.util.Log.i("HealthProbe", "PROBE_ERR perm $name ${t.javaClass.simpleName}: ${t.message}")
                    null
                }
                if (perm == null) {
                    android.util.Log.i("HealthProbe", "PROBE_RESULT $name granted=false days_with_data=0 dps=0 range=-")
                    continue
                }
                if (name !in knownGranted) {
                    val reGranted = try {
                        store.getGrantedPermissions(setOf(perm)).contains(perm)
                    } catch (t: Throwable) {
                        android.util.Log.i("HealthProbe", "PROBE_ERR recheck $name ${t.javaClass.simpleName}: ${t.message}")
                        false
                    }
                    if (reGranted) knownGranted.add(name)
                }
                if (name !in knownGranted) {
                    android.util.Log.i("HealthProbe", "PROBE_RESULT $name granted=false days_with_data=0 dps=0 range=-")
                    continue
                }
                grantedKnown = true

                // 14 天扫描（单日异常只清零当日不断类型；扫描层逃逸异常由下方 catch 兜底）
                var daysWithData = 0
                var dps = 0
                var earliest: Instant? = null
                var latest: Instant? = null
                var d = today.minusDays(13)
                val end = today
                while (!d.isAfter(end)) {
                    val dayDps = try {
                        readDayPoints(store, dt, d.atStartOfDay(), d.plusDays(1).atStartOfDay())
                    } catch (t: Throwable) {
                        android.util.Log.i("HealthProbe", "PROBE_ERR read $name $d ${t.javaClass.simpleName}: ${t.message}")
                        emptyList()
                    }
                    var dayNonNull = 0
                    for (dp in dayDps) {
                        val st = dpStart(dp)
                        if (st != null) {
                            dayNonNull++
                            if (earliest == null || st.isBefore(earliest)) earliest = st
                            if (latest == null || st.isAfter(latest)) latest = st
                        }
                    }
                    if (dayNonNull > 0) daysWithData++
                    dps += dayNonNull
                    d = d.plusDays(1)
                }
                val range = if (dps == 0) "-" else "$earliest..$latest"
                android.util.Log.i("HealthProbe", "PROBE_RESULT $name granted=true days_with_data=$daysWithData dps=$dps range=$range")
            } catch (t: CancellationException) {
                throw t // 保持结构化取消语义
            } catch (t: Throwable) {
                // 单类型整体失败：降级为 0 汇总 + err= 后缀，继续其余类型
                val errDetail = "${t.javaClass.simpleName}: ${t.message}"
                android.util.Log.i("HealthProbe", "PROBE_RESULT $name granted=$grantedKnown days_with_data=0 dps=0 range=- err=$errDetail")
            }
        }
    }

    // ================= Debug-only: STEPS/ACTIVITY_SUMMARY deep probe (intent extra "deep") =================
    // Triggered ONLY by `--ez deep true`. Probes STEPS / ACTIVITY_SUMMARY / FLOORS_CLIMBED / EXERCISE with a
    // 60-day wide-window point scan, plus a best-effort ReadSourceFilter re-read when a type comes back empty,
    // plus up to 3 sample datapoints per type with data. Logcat tag "HealthProbe".
    // SDK context (data-1.0.0.aar, verified via javap): StepsType/ActivitySummaryType are AGGREGATE-ONLY —
    // they implement no Readable, expose no getReadDataRequestBuilder and no Field constants (only
    // AggregateOperation statics), so the generic point read (readDayPoints) can never return data for them.

    private fun deepTypes(): List<Pair<String, DataType>> = listOf(
        "STEPS" to DataTypes.STEPS,
        "ACTIVITY_SUMMARY" to DataTypes.ACTIVITY_SUMMARY,
        "FLOORS_CLIMBED" to DataTypes.FLOORS_CLIMBED,
        "EXERCISE" to DataTypes.EXERCISE
    )

    private suspend fun runDeepProbe() {
        try {
            withContext(Dispatchers.IO) { deepProbeAll() }
        } catch (t: CancellationException) {
            throw t // 保持结构化取消语义
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR runDeep ${t.javaClass.simpleName}: ${t.message}")
        } finally {
            // 终止标记保证必达
            android.util.Log.i("HealthProbe", "DEEP_DONE")
        }
    }

    private suspend fun deepProbeAll() {
        val store = try {
            HealthDataService.getStore(applicationContext)
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR store ${t.javaClass.simpleName}: ${t.message}")
            return
        }

        val types = deepTypes()

        // 1) 授权检查 + 单次请求所有缺失项（吸收失败；任何异常都不中断）
        val want = types.map { (_, dt) -> Permission.of(dt, AccessType.READ) }
        val missing = try {
            store.getGrantedPermissions(want.toSet()).let { granted -> want.filter { it !in granted } }
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR grant ${t.javaClass.simpleName}: ${t.message}")
            want
        }
        if (missing.isNotEmpty()) {
            try {
                store.requestPermissions(missing.toSet(), this@MainActivity)
            } catch (t: Throwable) {
                android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR request ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        // 请求后再取一次最终授权集合（用户可能在弹窗中拒绝部分权限）
        val grantedPerms = try {
            store.getGrantedPermissions(want.toSet())
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR regrant ${t.javaClass.simpleName}: ${t.message}")
            emptySet()
        }

        // 2) 60 天宽窗口：单次窗口读取 [today-59, today+1)（而不是 14 天逐日循环）
        val startLdt = LocalDate.now().minusDays(59).atStartOfDay()
        val endLdt = LocalDate.now().plusDays(1).atStartOfDay()
        val windowDays = ChronoUnit.DAYS.between(startLdt.toLocalDate(), endLdt.toLocalDate())

        for ((name, dt) in types) {
            try {
                val perm = try {
                    Permission.of(dt, AccessType.READ)
                } catch (t: Throwable) {
                    android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR perm $name ${t.javaClass.simpleName}: ${t.message}")
                    null
                }
                val granted = perm != null && perm in grantedPerms
                if (!granted) {
                    android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR grantState $name not-granted-after-request")
                }

                val wide = try {
                    readDayPoints(store, dt, startLdt, endLdt)
                } catch (t: Throwable) {
                    android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR read60 $name ${t.javaClass.simpleName}: ${t.message}")
                    emptyList()
                }
                val wideStats = deepStats(wide)
                android.util.Log.i(
                    "HealthProbe",
                    "DEEP_RESULT $name window_days=$windowDays dps=${wideStats.dps} days=${wideStats.days} range=${wideStats.range}"
                )

                // 3) 宽窗口为空时，对 STEPS / ACTIVITY_SUMMARY 追加 ReadSourceFilter 二次点读（best-effort）
                var filterDps: List<HealthDataPoint> = emptyList()
                if (wideStats.dps == 0 && (name == "STEPS" || name == "ACTIVITY_SUMMARY")) {
                    filterDps = deepFilterRead(store, dt, startLdt, endLdt)
                    val fStats = deepStats(filterDps)
                    android.util.Log.i("HealthProbe", "DEEP_FILTER_READ $name dps=${fStats.dps}")
                }

                // 4) 有数据时抽取最多 3 个样本点（宽窗口优先，其次 filter 结果）
                val sampleSource = if (wideStats.dps > 0) wide else filterDps
                emitDeepSamples(name, dt, sampleSource)
            } catch (t: CancellationException) {
                throw t // 保持结构化取消语义
            } catch (t: Throwable) {
                // 单类型失败不中断其余类型
                android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR type $name ${t.javaClass.simpleName}: ${t.message}")
            }
        }
    }

    /**
     * Best-effort secondary point-read with ReadSourceFilter applied (STEPS / ACTIVITY_SUMMARY only).
     * SDK notes (data-1.0.0.aar): no DataSource class, no HealthDataStore.getDataSources(), no
     * ReadSourceFilter.fromAllSources() exist; factories available are fromPlatform() / fromLocalDevice() /
     * fromDeviceType(DeviceType) / of(appId, DeviceType) / fromApplicationId(appId); the concrete request
     * builders expose setSourceFilter(ReadSourceFilter). StepsType/ActivitySummaryType have NO
     * getReadDataRequestBuilder in this SDK (aggregate-only), so this path is expected to fail for them:
     * each failure logs PROBE_DEEP_ERR and returns empty.
     */
    @Suppress("UNCHECKED_CAST")
    private suspend fun deepFilterRead(
        store: HealthDataStore,
        dt: DataType,
        startLdt: LocalDateTime,
        endLdt: LocalDateTime
    ): List<HealthDataPoint> {
        // builder 是点读前提：无 getReadDataRequestBuilder 的类型（STEPS/ACTIVITY_SUMMARY）直接记录并返回空
        val builderRef = try {
            dt.javaClass.getMethod("getReadDataRequestBuilder")
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR filter $dt no-point-builder ${t.javaClass.simpleName}: ${t.message}")
            return emptyList()
        }
        val filters = try {
            listOf(
                com.samsung.android.sdk.health.data.request.ReadSourceFilter.fromPlatform(),
                com.samsung.android.sdk.health.data.request.ReadSourceFilter.fromLocalDevice()
            )
        } catch (t: Throwable) {
            android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR filterFactory $dt ${t.javaClass.simpleName}: ${t.message}")
            return emptyList()
        }
        val rsfClass = com.samsung.android.sdk.health.data.request.ReadSourceFilter::class.java
        for (f in filters) {
            try {
                // 每个候选重建一次 builder（build() 后 builder 不可复用）
                val builder = builderRef.invoke(dt)
                val bc = builder.javaClass
                try { bc.getMethod("setLimit", Int::class.java).invoke(builder, 10000) } catch (_: Throwable) {}
                try {
                    bc.getMethod("setLocalTimeFilter", LocalTimeFilter::class.java)
                        .invoke(builder, LocalTimeFilter.of(startLdt, endLdt))
                } catch (_: Throwable) {}
                bc.getMethod("setSourceFilter", rsfClass).invoke(builder, f)
                val req = bc.getMethod("build").invoke(builder) as com.samsung.android.sdk.health.data.request.ReadDataRequest<HealthDataPoint>
                val resp = store.readData(req)
                val list = (resp.dataList as? List<HealthDataPoint>) ?: emptyList()
                if (list.isNotEmpty()) return list
            } catch (t: Throwable) {
                android.util.Log.i("HealthProbe", "PROBE_DEEP_ERR filter $dt ${t.javaClass.simpleName}: ${t.message}")
            }
        }
        return emptyList()
    }

    /** 逐类型代表性值字段常量名（对 data-1.0.0.aar javap 验证过的真实名称）。 */
    private fun deepSampleFields(typeName: String): List<String> = when (typeName) {
        "FLOORS_CLIMBED" -> listOf("FLOOR")
        "EXERCISE" -> listOf("EXERCISE_TYPE", "CUSTOM_TITLE", "SESSIONS")
        // STEPS / ACTIVITY_SUMMARY：StepsType / ActivitySummaryType 在该 SDK 中没有任何 Field 常量
        // （仅有 TOTAL / TOTAL_* 这类 AggregateOperation），逐点无可读字段。
        else -> emptyList()
    }

    private fun deepFieldSummary(dt: DataType, dp: HealthDataPoint, fieldName: String): String {
        val v = getField<Any>(dt, fieldName, dp)
        val text = when (v) {
            null -> "null"
            is List<*> -> "size=${v.size}"
            else -> v.toString()
        }
        return "$fieldName=$text"
    }

    /** dps=非空 start 的样本数；days=这些 start 覆盖的不同本地日数；range=最早..最晚 ISO（无数据为 -）。 */
    private class DeepStats(val dps: Int, val days: Int, val range: String)

    private fun deepStats(list: List<HealthDataPoint>): DeepStats {
        var count = 0
        var earliest: Instant? = null
        var latest: Instant? = null
        val dateSet = HashSet<LocalDate>()
        for (dp in list) {
            val st = dpStart(dp) ?: continue
            count++
            dateSet.add(st.atZone(ZoneId.systemDefault()).toLocalDate())
            if (earliest == null || st.isBefore(earliest)) earliest = st
            if (latest == null || st.isAfter(latest)) latest = st
        }
        val range = if (count == 0) "-" else "$earliest..$latest"
        return DeepStats(count, dateSet.size, range)
    }

    /** 最多输出 3 行 DEEP_SAMPLE（按 start 升序）。 */
    private fun emitDeepSamples(name: String, dt: DataType, dps: List<HealthDataPoint>) {
        val samples = dps.mapNotNull { dp -> dpStart(dp)?.let { dp to it } }
            .sortedBy { it.second }
            .take(3)
        for ((dp, st) in samples) {
            val fields = deepSampleFields(name)
            val parts = ArrayList<String>()
            parts.add("start=$st")
            if (fields.isEmpty()) {
                parts.add("fields=<none>") // 该类型在 SDK 中无 Field 常量（aggregate-only）
            } else {
                for (f in fields) parts.add(deepFieldSummary(dt, dp, f))
            }
            android.util.Log.i("HealthProbe", "DEEP_SAMPLE $name ${parts.joinToString(" ")}")
        }
    }

    companion object {
        private const val MAX_ROWS = 45
        private const val UPLOAD_URL = "http://192.168.137.1:8899/upload"
        // 提醒时间读写统一走 ReminderScheduler（PREFS/KEY/REQUEST_CODE 由其持有）
        private const val PREFS_APP = "app_prefs" // 通知去重用（与 ReminderScheduler 同一 prefs 文件）
        private const val KEY_LAST_NOTIFIED_N = "last_notified_n"
    }
}
