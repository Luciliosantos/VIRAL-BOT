import httpx

from config import SMM_API_URL, SMM_API_KEY


class SMMAPIError(Exception):
    pass


class SMMClient:
    def __init__(self, base_url=None, api_key=None):
        self.base_url = (base_url or SMM_API_URL).strip()
        self.api_key = (api_key or SMM_API_KEY).strip()

        if not self.base_url or not self.api_key:
            raise SMMAPIError(
                "Configure SMM_API_URL e SMM_API_KEY no arquivo .env."
            )

    async def request(self, action, **params):
        payload = {
            "key": self.api_key,
            "action": action,
            **params,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.base_url,
                    data=payload,
                )
                response.raise_for_status()

        except httpx.HTTPError as exc:
            raise SMMAPIError(
                "Falha de comunicação com a API SMM."
            ) from exc

        try:
            result = response.json()
        except ValueError as exc:
            raise SMMAPIError(
                "A API retornou uma resposta que não é JSON."
            ) from exc

        if isinstance(result, dict) and result.get("error"):
            raise SMMAPIError(str(result["error"]))

        return result

    async def listar_servicos(self):
        result = await self.request("services")

        if not isinstance(result, list):
            raise SMMAPIError(
                "Resposta inesperada ao consultar os serviços."
            )

        return result

    async def consultar_pedido(self, order_id):
        return await self.request("status", order=order_id)

    async def consultar_saldo_fornecedor(self):
        return await self.request("balance")

    async def criar_pedido(self, service_id, link, quantity=None, comments=None):
        """Cria pedido padrão ou de comentários personalizados autorizados."""
        service_id = str(service_id).strip()
        link = str(link).strip()

        if not service_id:
            raise SMMAPIError("ID do serviço inválido.")
        if not link.startswith(("https://", "http://")):
            raise SMMAPIError("O link precisa começar com http:// ou https://.")

        params = {"service": service_id, "link": link}

        if comments is not None:
            if isinstance(comments, str):
                linhas = [linha.strip() for linha in comments.splitlines() if linha.strip()]
            else:
                linhas = [str(linha).strip() for linha in comments if str(linha).strip()]

            if not linhas:
                raise SMMAPIError("Informe pelo menos um comentário.")
            params["comments"] = "\n".join(linhas)
        else:
            try:
                quantity = int(quantity)
            except (TypeError, ValueError) as exc:
                raise SMMAPIError("A quantidade precisa ser um número inteiro.") from exc
            if quantity <= 0:
                raise SMMAPIError("A quantidade precisa ser maior que zero.")
            params["quantity"] = quantity

        result = await self.request("add", **params)
        if not isinstance(result, dict) or not result.get("order"):
            raise SMMAPIError("A API não confirmou a criação do pedido.")
        return result

    async def solicitar_reposicao(self, provider_order_id):
        """Solicita reposição usando o ID do pedido no fornecedor."""
        provider_order_id = str(provider_order_id or "").strip()
        if not provider_order_id.isdigit():
            raise SMMAPIError("ID do pedido do fornecedor inválido.")

        result = await self.request(
            "refill",
            order=provider_order_id,
        )

        if not isinstance(result, dict) or not result.get("refill"):
            raise SMMAPIError(
                "O fornecedor não confirmou a solicitação de reposição."
            )

        refill_id = str(result["refill"]).strip()
        if not refill_id.isdigit():
            raise SMMAPIError(
                "A API retornou um ID de reposição inválido."
            )

        return {"refill": refill_id}

    async def consultar_status_reposicao(self, refill_id):
        """Consulta o estado de uma reposição pelo ID do fornecedor."""
        refill_id = str(refill_id or "").strip()
        if not refill_id.isdigit():
            raise SMMAPIError("ID da reposição inválido.")

        result = await self.request(
            "refill_status",
            refill=refill_id,
        )

        if not isinstance(result, dict) or not result.get("status"):
            raise SMMAPIError(
                "O fornecedor não retornou o status da reposição."
            )

        return result
