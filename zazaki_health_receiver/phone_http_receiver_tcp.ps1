# phone_http_receiver_tcp.ps1 — Windows-side HTTP receiver (TcpListener, no http.sys/ACL needed).
# 手机 HealthReader → POST http://192.168.137.1:8899/upload[/<文件名>] (raw JSON body)
#   POST /upload/<YYYYMMDD_sleep_hr.json> → 存到 data\<文件名>（同日重传覆盖 = 幂等，供 ⑦ AI 分析按日取用）
#   POST /upload（无文件名，旧版兼容）      → 存到 data\health_<timestamp>.json
#   两种路径都同时写 data\_latest.json
#
# Run in Windows PowerShell (mirrors the proven ws_forward.ps1 approach):
#   powershell -ExecutionPolicy Bypass -File phone_http_receiver_tcp.ps1
param(
    [int]$Port = 8899,
    [string]$ListenIp = "192.168.137.1"
)

# --- analyzer auto-trigger (task A7) ---
$AnalyzerPython = "C:\Users\hzj\AppData\Local\Python\bin\python.exe"
$AnalyzerDir = "C:\Users\hzj\zazaki_health\analyzer"

$dataDir = Join-Path $PSScriptRoot "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

public static class HttpRecv
{
    public static void Start(string ip, int port, string dataDir, string analyzerPython, string analyzerDir)
    {
        var listener = new TcpListener(IPAddress.Parse(ip), port);
        listener.Start();
        Console.WriteLine("[recv] listening on " + ip + ":" + port + " -> " + dataDir);
        while (true)
        {
            TcpClient client = null;
            try { client = listener.AcceptTcpClient(); }
            catch { break; }
            var c = client; var d = dataDir;
            var t = new Thread(() => Handle(c, d, analyzerPython, analyzerDir));
            t.IsBackground = true;
            t.Start();
        }
    }

    static void Handle(TcpClient client, string dataDir, string analyzerPython, string analyzerDir)
    {
        try
        {
            var stream = client.GetStream();
            // read request line + headers until CRLFCRLF
            var headerBuf = new MemoryStream();
            var one = new byte[1];
            int crlf = 0; // count consecutive CRLFCRLF
            while (crlf < 4)
            {
                int n = stream.Read(one, 0, 1);
                if (n <= 0) { client.Close(); return; }
                headerBuf.Write(one, 0, 1);
                byte b = one[0];
                if (b == 13 || b == 10) { /* CR or LF */ }
                // detect "\r\n\r\n" pattern: track last four bytes
                var arr = headerBuf.ToArray();
                int len = arr.Length;
                if (len >= 4 && arr[len-4]==13 && arr[len-3]==10 && arr[len-2]==13 && arr[len-1]==10) { crlf = 4; break; }
            }
            var headerText = Encoding.UTF8.GetString(headerBuf.ToArray());
            var lines = headerText.Split(new[]{"\r\n"}, StringSplitOptions.None);
            string reqLine = lines.Length > 0 ? lines[0] : "";
            var parts = reqLine.Split(' ');
            string method = parts.Length > 0 ? parts[0] : "";
            string path = parts.Length > 1 ? parts[1] : "";
            // content-length
            int contentLength = 0;
            foreach (var ln in lines)
            {
                if (ln.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase))
                {
                    int.TryParse(ln.Substring("Content-Length:".Length).Trim(), out contentLength);
                }
            }
            bool ok = false;
            string respBody;
            if (method == "POST" && (path == "/upload" || path.StartsWith("/upload/", StringComparison.Ordinal)))
            {
                var bodyBuf = new byte[contentLength];
                int got = 0;
                while (got < contentLength)
                {
                    int r = stream.Read(bodyBuf, got, contentLength - got);
                    if (r <= 0) break;
                    got += r;
                }
                string body = Encoding.UTF8.GetString(bodyBuf, 0, got);
                // validate JSON
                try { var _ = NewtonsoftLike(body); ok = !String.IsNullOrWhiteSpace(body); } catch { ok = false; }
                if (ok)
                {
                    // 文件名优先取 URL 路径末段（/upload/<YYYYMMDD_sleep_hr.json>），同名覆盖 = 同日幂等
                    string fname = null;
                    if (path.StartsWith("/upload/", StringComparison.Ordinal))
                    {
                        fname = path.Substring("/upload/".Length).Replace('\\', '/');
                        int slash = fname.LastIndexOf('/');
                        if (slash >= 0) fname = fname.Substring(slash + 1);
                    }
                    // 清洗：仅允许 [A-Za-z0-9_.-]+，拒绝路径分隔/.. /超长，否则回退时间戳名（兼容旧 /upload）
                    bool nameOk = !String.IsNullOrEmpty(fname) && fname.Length <= 64 &&
                        !fname.Contains("..") &&
                        System.Text.RegularExpressions.Regex.IsMatch(fname, "^[A-Za-z0-9_.-]+$");
                    string savedName = nameOk
                        ? fname
                        : "health_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".json";
                    string p = Path.Combine(dataDir, savedName);
                    File.WriteAllText(p, body, new UTF8Encoding(false));
                    File.WriteAllText(Path.Combine(dataDir, "_latest.json"), body, new UTF8Encoding(false));
                    Console.WriteLine("[" + DateTime.Now.ToString("HH:mm:ss") + "] saved " + savedName + " (" + got + " bytes)");
                    // A7 hook: fire-and-forget analyzer for by-day uploads (never breaks save/response)
                    if (System.Text.RegularExpressions.Regex.IsMatch(savedName, "^\\d{8}_sleep_hr\\.json$"))
                    {
                        try
                        {
                            var psi = new System.Diagnostics.ProcessStartInfo();
                            psi.FileName = analyzerPython;
                            psi.Arguments = "\"" + System.IO.Path.Combine(analyzerDir, "analyzer.py") + "\" --file \"" + p + "\"";
                            psi.UseShellExecute = false;
                            psi.CreateNoWindow = true;
                            System.Diagnostics.Process.Start(psi);
                            Console.WriteLine("[" + DateTime.Now.ToString("HH:mm:ss") + "] analyzer spawned for " + savedName);
                        }
                        catch (Exception e2)
                        {
                            Console.WriteLine("[recv] analyzer spawn failed: " + e2.Message);
                        }
                    }
                    respBody = "{\"ok\":true,\"file\":\"" + savedName + "\",\"size\":" + got + "}";
                }
                else respBody = "{\"ok\":false,\"error\":\"invalid json\"}";
            }
            else respBody = "{\"ok\":false,\"error\":\"not found\"}";

            var respBytes = Encoding.UTF8.GetBytes(respBody);
            var sb = new StringBuilder();
            sb.Append("HTTP/1.1 ").Append(ok ? "200 OK" : "400 Bad Request").Append("\r\n");
            sb.Append("Content-Type: application/json\r\n");
            sb.Append("Content-Length: ").Append(respBytes.Length).Append("\r\n");
            sb.Append("Connection: close\r\n\r\n");
            var headBytes = Encoding.ASCII.GetBytes(sb.ToString());
            stream.Write(headBytes, 0, headBytes.Length);
            stream.Write(respBytes, 0, respBytes.Length);
            stream.Flush();
        }
        catch (Exception e)
        {
            Console.WriteLine("[recv] error: " + e.Message);
        }
        finally { client.Close(); }
    }

    // minimal JSON sanity: first non-ws char must be { or [
    static bool NewtonsoftLike(string s)
    {
        if (String.IsNullOrWhiteSpace(s)) return false;
        string t = s.TrimStart();
        return t.Length > 0 && (t[0] == '{' || t[0] == '[');
    }
}
'@

[HttpRecv]::Start($ListenIp, $Port, $dataDir, $AnalyzerPython, $AnalyzerDir)
Write-Host "receiver exited"
