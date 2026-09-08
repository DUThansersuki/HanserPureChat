using System.Net.Http.Json;
using System.Text.Json.Serialization;

namespace HanserWpf;

public sealed class PythonAgentClient
{
    private readonly HttpClient _http;

    public PythonAgentClient(string baseUrl = "http://127.0.0.1:8765")
    {
        _http = new HttpClient
        {
            BaseAddress = new Uri(baseUrl),
            Timeout = TimeSpan.FromMinutes(10),
        };
    }

    public async Task<PythonAgentResponse> SendAsync(
        string conversationId,
        string message,
        CancellationToken cancellationToken = default)
    {
        var response = await _http.PostAsJsonAsync(
            "/v1/chat",
            new
            {
                conversation_id = conversationId,
                message,
            },
            cancellationToken);

        var body = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException($"Python Agent 调用失败（{(int)response.StatusCode}）：{body}");

        return System.Text.Json.JsonSerializer.Deserialize<PythonAgentResponse>(body)
               ?? throw new InvalidOperationException("Python Agent 返回为空");
    }
}

public sealed class PythonAgentResponse
{
    [JsonPropertyName("text")]
    public string Text { get; set; } = "";

    [JsonPropertyName("keywords")]
    public List<string> Keywords { get; set; } = new();

    [JsonPropertyName("anchored")]
    public List<string> Anchored { get; set; } = new();

    [JsonPropertyName("sources")]
    public List<PythonSearchResult> Sources { get; set; } = new();
}

public sealed class PythonSearchResult
{
    [JsonPropertyName("id")]
    public long Id { get; set; }

    [JsonPropertyName("filename")]
    public string Filename { get; set; } = "";

    [JsonPropertyName("filepath")]
    public string Filepath { get; set; } = "";

    [JsonPropertyName("hits")]
    public int Hits { get; set; }

    [JsonPropertyName("matched")]
    public List<string> Matched { get; set; } = new();

    [JsonPropertyName("snippet")]
    public string Snippet { get; set; } = "";

    [JsonPropertyName("score")]
    public double Score { get; set; }
}
