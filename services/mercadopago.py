import os
import uuid
from decimal import Decimal, ROUND_HALF_UP

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.mercadopago.com"


class MercadoPagoError(Exception):
    pass


class MercadoPago:
    def __init__(self):
        self.token = os.getenv("MP_ACCESS_TOKEN", "").strip()

        if not self.token:
            raise MercadoPagoError(
                "MP_ACCESS_TOKEN não está configurado no .env."
            )

    async def _request(self, method, path, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        headers.update(kwargs.pop("headers", {}))

        try:
            async with httpx.AsyncClient(timeout=25) as client:
                response = await client.request(
                    method,
                    f"{BASE_URL}{path}",
                    headers=headers,
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise MercadoPagoError(
                "Não foi possível conectar ao Mercado Pago."
            ) from exc

        try:
            data = response.json()
        except ValueError:
            data = {}

        if not response.is_success:
            # Não exibe token nem cabeçalhos de autenticação.
            raise MercadoPagoError(
                f"Mercado Pago recusou a solicitação "
                f"(HTTP {response.status_code})."
            )

        return data

    async def criar_pix(self, valor, descricao, email):
        valor = Decimal(str(valor)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        email = str(email).strip()

        if valor <= 0:
            raise MercadoPagoError("O valor do Pix deve ser maior que zero.")

        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            raise MercadoPagoError("Informe um e-mail válido para o Pix.")

        dados = await self._request(
            "POST",
            "/v1/payments",
            headers={
                "X-Idempotency-Key": str(uuid.uuid4()),
            },
            json={
                "transaction_amount": float(valor),
                "description": str(descricao)[:200],
                "payment_method_id": "pix",
                "payer": {
                    "email": email,
                },
            },
        )

        transacao = (
            dados.get("point_of_interaction", {})
            .get("transaction_data", {})
        )

        payment_id = dados.get("id")
        codigo_pix = transacao.get("qr_code")

        if not payment_id or not codigo_pix:
            raise MercadoPagoError(
                "O Mercado Pago não retornou os dados completos do Pix."
            )

        return {
            "payment_id": str(payment_id),
            "status": dados.get("status", "pending"),
            "valor": str(valor),
            "pix_copia_cola": codigo_pix,
            "qr_code_base64": transacao.get("qr_code_base64"),
        }

    async def consultar_pagamento(self, payment_id):
        payment_id = str(payment_id).strip()

        if not payment_id.isdigit():
            raise MercadoPagoError("Identificador de pagamento inválido.")

        dados = await self._request(
            "GET",
            f"/v1/payments/{payment_id}",
        )

        return {
            "payment_id": str(dados.get("id", "")),
            "status": dados.get("status", ""),
            "valor": dados.get("transaction_amount"),
            "metodo": dados.get("payment_method_id", ""),
        }
