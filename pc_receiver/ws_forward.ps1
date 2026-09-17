# ws_forward.ps1 - Watch7 pipeline TCP forwarder (replaces broken netsh portproxy)
# Listens on 192.168.137.1:8766 (Windows ICS host, reachable from the watch LAN)
# and relays every byte stream to 127.0.0.1:8767 (WSL python receiver; WSL is mirrored-networking,
# so the WSL receiver's own port must differ from the Windows listener port).
# Usage:  powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Arch\home\zazaki\Projects\zazaki_health\pc_receiver\ws_forward.ps1
# Requires: WSL receiver running (python3 receiver.py --port 8767 in WSL), firewall rule for TCP 8766 in place.

Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;

public static class WsForwarder
{
    public static void Start(string listenIp, int listenPort, string targetHost, int targetPort)
    {
        var listener = new TcpListener(IPAddress.Parse(listenIp), listenPort);
        listener.Start();
        Console.WriteLine("[fwd] listening on {0}:{1} -> {2}:{3}  (Ctrl+C to stop)", listenIp, listenPort, targetHost, targetPort);
        while (true)
        {
            TcpClient client = null;
            try { client = listener.AcceptTcpClient(); }
            catch { break; } // listener stopped
            var c = client;
            var t = new Thread(() => Handle(c, targetHost, targetPort));
            t.IsBackground = true;
            t.Start();
        }
    }

    static void Handle(TcpClient client, string targetHost, int targetPort)
    {
        try
        {
            using (var upstream = new TcpClient())
            {
                upstream.Connect(targetHost, targetPort);
                var cs = client.GetStream();
                var us = upstream.GetStream();
                Console.WriteLine("[fwd] client {0} connected", client.Client.RemoteEndPoint);
                var t1 = new Thread(() => Pump(cs, us));
                var t2 = new Thread(() => Pump(us, cs));
                t1.IsBackground = true; t2.IsBackground = true;
                t1.Start(); t2.Start();
                t1.Join(); t2.Join();
                Console.WriteLine("[fwd] client {0} disconnected", client.Client.RemoteEndPoint);
            }
        }
        catch (Exception e)
        {
            Console.WriteLine("[fwd] error: " + e.Message);
        }
        finally { client.Close(); }
    }

    static void Pump(NetworkStream src, NetworkStream dst)
    {
        var buf = new byte[8192];
        try
        {
            while (true)
            {
                int n = src.Read(buf, 0, buf.Length);
                if (n <= 0) break;
                dst.Write(buf, 0, n);
                dst.Flush();
            }
        }
        catch { }
        try { src.Close(); } catch { }
        try { dst.Close(); } catch { }
    }
}
'@

[WsForwarder]::Start('192.168.137.1', 8766, '127.0.0.1', 8767)
