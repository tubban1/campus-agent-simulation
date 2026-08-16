import unittest
from unittest.mock import patch, MagicMock
from services.llm_service import (
    LLMFactory,
    OpenAIProvider,
    GeminiProvider,
    OllamaProvider,
    AnthropicProvider,
    ask_llm,
    is_llm_configured,
    get_llm_provider_info,
)


class TestLLMFactory(unittest.TestCase):

    def test_openai_provider_prepare_request(self):
        provider = OpenAIProvider(
            api_key="sk-testkey",
            api_url="https://api.deepseek.com",
            model="deepseek-chat",
        )
        url, headers, body = provider.prepare_request("Hello AI")

        self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer sk-testkey")
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["messages"], [{"role": "user", "content": "Hello AI"}])

    def test_openai_provider_parse_response(self):
        provider = OpenAIProvider()
        mock_data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "DeepSeek Response Text",
                    }
                }
            ]
        }
        result = provider.parse_response(mock_data)
        self.assertEqual(result, "DeepSeek Response Text")

    def test_gemini_provider_prepare_request(self):
        provider = GeminiProvider(
            api_key="gemini-key-123",
            api_url="https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
            model="gemini-1.5-flash",
        )
        url, headers, body = provider.prepare_request("Analyze this context")

        self.assertIn("googleapis.com", url)
        self.assertEqual(headers["x-goog-api-key"], "gemini-key-123")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "Analyze this context")

    def test_gemini_provider_parse_response(self):
        provider = GeminiProvider()
        mock_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Gemini Response Text"}]
                    }
                }
            ]
        }
        result = provider.parse_response(mock_data)
        self.assertEqual(result, "Gemini Response Text")

    def test_ollama_provider_prepare_request(self):
        provider = OllamaProvider(
            api_url="http://localhost:11434/api/chat",
            model="llama3.1",
        )
        url, headers, body = provider.prepare_request("Local prompt")

        self.assertEqual(url, "http://localhost:11434/api/chat")
        self.assertEqual(body["model"], "llama3.1")
        self.assertFalse(body["stream"])

    def test_ollama_provider_parse_response(self):
        provider = OllamaProvider()
        mock_data = {"message": {"content": "Ollama Answer"}}
        self.assertEqual(provider.parse_response(mock_data), "Ollama Answer")

    def test_anthropic_provider_prepare_request(self):
        provider = AnthropicProvider(
            api_key="claude-key",
            model="claude-3-5-sonnet-20241022",
        )
        url, headers, body = provider.prepare_request("Claude prompt")

        self.assertEqual(headers["x-api-key"], "claude-key")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(body["model"], "claude-3-5-sonnet-20241022")

    def test_llm_factory_auto_detection(self):
        # OpenAI Provider auto detection by URL
        p1 = LLMFactory.get_provider(
            provider_name="",
            api_url="https://api.deepseek.com/v1/chat/completions",
        )
        self.assertIsInstance(p1, OpenAIProvider)

        # Gemini Provider auto detection by URL
        p2 = LLMFactory.get_provider(
            provider_name="",
            api_url="https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
        )
        self.assertIsInstance(p2, GeminiProvider)

        # Ollama Provider auto detection by URL
        p3 = LLMFactory.get_provider(
            provider_name="",
            api_url="http://localhost:11434/api/chat",
        )
        self.assertIsInstance(p3, OllamaProvider)

    def test_ask_llm_with_mock(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Mocked LLM Result"}}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp):
            with patch.dict("os.environ", {"LLM_PROVIDER": "openai", "LLM_API_KEY": "test-key", "LLM_API_URL": "https://api.test.com"}):
                ans = ask_llm("Test prompt")
                self.assertEqual(ans, "Mocked LLM Result")

    def test_get_llm_provider_info(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-4o", "LLM_API_KEY": "test"}):
            info = get_llm_provider_info()
            self.assertEqual(info["provider_class"], "OpenAIProvider")
            self.assertEqual(info["model"], "gpt-4o")
            self.assertEqual(info["is_configured"], "True")

    def test_vendor_prefixed_env_switching(self):
        env_dict = {
            "DEFAULT_LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-deepseek-123",
            "DEEPSEEK_API_URL": "https://api.deepseek.com/v1/chat/completions",
            "DEEPSEEK_MODEL": "deepseek-chat",
            "GEMINI_API_KEY": "gemini-key-456",
            "GEMINI_MODEL": "gemini-1.5-flash",
        }
        with patch.dict("os.environ", env_dict, clear=True):
            # Test default switch to deepseek
            provider_ds = LLMFactory.get_provider()
            self.assertIsInstance(provider_ds, OpenAIProvider)
            self.assertEqual(provider_ds.api_key, "sk-deepseek-123")
            self.assertEqual(provider_ds.model, "deepseek-chat")

        with patch.dict("os.environ", {**env_dict, "DEFAULT_LLM_PROVIDER": "gemini"}, clear=True):
            # Test switch default to gemini
            provider_gemini = LLMFactory.get_provider()
            self.assertIsInstance(provider_gemini, GeminiProvider)
            self.assertEqual(provider_gemini.api_key, "gemini-key-456")
            self.assertEqual(provider_gemini.model, "gemini-1.5-flash")

    def test_admin_token_vendor_switching(self):
        env_dict = {
            "DEFAULT_LLM_PROVIDER": "vllm",
            "VLLM_API_KEY": "sk-vllm-key",
            "VLLM_API_URL": "https://api.vllmproxy.com/v1/chat/completions",
            "VLLM_MODEL": "gemini-3.1-flash-lite",
            "VLLM_ADMIN_TOKEN": "sk-vllm-admin-token",
        }
        with patch.dict("os.environ", env_dict, clear=True):
            provider = LLMFactory.get_provider()
            self.assertIsInstance(provider, OpenAIProvider)
            self.assertEqual(provider.admin_token, "sk-vllm-admin-token")
            url, headers, body = provider.prepare_request("Test vllm")
            self.assertEqual(headers["Authorization"], "Bearer sk-vllm-key")
            self.assertEqual(headers["x-admin-token"], "sk-vllm-admin-token")


if __name__ == "__main__":
    unittest.main()
