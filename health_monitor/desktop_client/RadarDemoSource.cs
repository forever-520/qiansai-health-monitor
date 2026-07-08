using System.Globalization;

namespace HealthMonitorClient;

public sealed record RadarFrame(byte Control, byte Command, byte[] Data, byte[] Raw);

public sealed record RadarDemoSnapshot(
    int HeartRate,
    int BreathRate,
    int MotionLevel,
    IReadOnlyList<short> HeartWave,
    IReadOnlyList<short> BreathWave,
    int FrameCount);

public sealed class RadarDemoSource
{
    private readonly List<short> _heartWave = [];
    private readonly List<short> _breathWave = [];
    private int _frameCount;

    public RadarDemoSnapshot Next(double phase)
    {
        var frames = new[]
        {
            RadarProtocol.BuildFrame(0x80, 0x01, [0x01]),
            RadarProtocol.BuildFrame(0x80, 0x03, [(byte)Clamp(26 + Math.Round(Math.Sin(phase / 55) * 7), 0, 100)]),
            RadarProtocol.BuildFrame(0x85, 0x02, [(byte)Clamp(72 + Math.Round(Math.Sin(phase / 80) * 2), 0, 255)]),
            RadarProtocol.BuildFrame(0x81, 0x02, [(byte)Clamp(16 + Math.Round(Math.Sin(phase / 120)), 0, 255)]),
            RadarProtocol.BuildFrame(0x85, 0x05, MakeWaveBytes(phase, slow: false)),
            RadarProtocol.BuildFrame(0x81, 0x05, MakeWaveBytes(phase * 0.45, slow: true)),
        };

        var heartRate = 0;
        var breathRate = 0;
        var motion = 0;

        foreach (var raw in frames)
        {
            _frameCount++;
            if (!RadarProtocol.TryParseFrame(raw, out var frame))
            {
                continue;
            }

            switch (frame)
            {
                case { Control: 0x80, Command: 0x03 } when frame.Data.Length == 1:
                    motion = frame.Data[0];
                    break;
                case { Control: 0x85, Command: 0x02 } when frame.Data.Length == 1:
                    heartRate = frame.Data[0];
                    break;
                case { Control: 0x81, Command: 0x02 } when frame.Data.Length == 1:
                    breathRate = frame.Data[0];
                    break;
                case { Control: 0x85, Command: 0x05 }:
                    AppendWave(_heartWave, frame.Data);
                    break;
                case { Control: 0x81, Command: 0x05 }:
                    AppendWave(_breathWave, frame.Data);
                    break;
            }
        }

        return new RadarDemoSnapshot(
            heartRate,
            breathRate,
            motion,
            _heartWave.ToArray(),
            _breathWave.ToArray(),
            _frameCount);
    }

    private static byte[] MakeWaveBytes(double phase, bool slow)
    {
        var bytes = new byte[5];
        for (var i = 0; i < bytes.Length; i++)
        {
            var t = (phase + i * 14) / (slow ? 56 : 22);
            var offset = Math.Sin(t) * (slow ? 38 : 24) + Math.Sin(t * (slow ? 0.42 : 2.8)) * (slow ? 8 : 12);
            bytes[i] = (byte)Clamp(128 + Math.Round(offset), 0, 255);
        }

        return bytes;
    }

    private static void AppendWave(List<short> buffer, byte[] data)
    {
        foreach (var value in data)
        {
            buffer.Add((short)(value - 128));
        }

        if (buffer.Count > 512)
        {
            buffer.RemoveRange(0, buffer.Count - 512);
        }
    }

    private static int Clamp(double value, int min, int max)
    {
        return Math.Min(max, Math.Max(min, (int)value));
    }
}

public static class RadarProtocol
{
    public static byte[] BuildFrame(byte control, byte command, byte[] data)
    {
        var frame = new byte[2 + 1 + 1 + 2 + data.Length + 1 + 2];
        frame[0] = 0x53;
        frame[1] = 0x59;
        frame[2] = control;
        frame[3] = command;
        frame[4] = (byte)(data.Length >> 8);
        frame[5] = (byte)(data.Length & 0xff);
        Array.Copy(data, 0, frame, 6, data.Length);
        frame[6 + data.Length] = CalcSum(frame.AsSpan(0, 6 + data.Length));
        frame[^2] = 0x54;
        frame[^1] = 0x43;
        return frame;
    }

    public static bool TryParseFrame(byte[] raw, out RadarFrame frame)
    {
        frame = new RadarFrame(0, 0, [], raw);
        if (raw.Length < 9 || raw[0] != 0x53 || raw[1] != 0x59 || raw[^2] != 0x54 || raw[^1] != 0x43)
        {
            return false;
        }

        var length = (raw[4] << 8) | raw[5];
        if (raw.Length != 9 + length)
        {
            return false;
        }

        var sumIndex = 6 + length;
        if (CalcSum(raw.AsSpan(0, sumIndex)) != raw[sumIndex])
        {
            return false;
        }

        var data = raw.Skip(6).Take(length).ToArray();
        frame = new RadarFrame(raw[2], raw[3], data, raw);
        return true;
    }

    public static string ToHex(byte[] raw)
    {
        return string.Join(' ', raw.Select(value => value.ToString("X2", CultureInfo.InvariantCulture)));
    }

    private static byte CalcSum(ReadOnlySpan<byte> bytes)
    {
        var sum = 0;
        foreach (var value in bytes)
        {
            sum += value;
        }

        return (byte)(sum & 0xff);
    }
}
