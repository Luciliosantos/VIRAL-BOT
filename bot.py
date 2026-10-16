from pathlib import Path
import logging
import asyncio
import os

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_IDS
USD_BRL_RATE = float(os.getenv("USD_BRL_RATE", "5.20"))
from database import consultar_saldo_usuario
from database import debitar_saldo_usuario, criar_pedido_com_saldo
from database import criar_recarga_local, creditar_recarga_aprovada
from database import (
    init_db,
    save_user,
    get_profit_percent,
    set_profit_percent,
)
from services.smm_api import SMMClient, SMMAPIError
from services.precos import calcular_preco
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse
from database import (
    criar_pedido_local, criar_pagamento_local,
    associar_pagamento_externo, buscar_pagamento,
    marcar_pagamento_aprovado, atualizar_status_pedido, connect
)
from services.precos import calcular_preco_por_mil
from services.mercadopago import MercadoPago, MercadoPagoError


import time
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

def menu_principal():
    redes = [
        ("📸 Instagram", "instagram"),
        ("🎵 TikTok", "tiktok"),
        ("▶️ YouTube", "youtube"),
        ("✈️ Telegram", "telegram"),
        ("📘 Facebook", "facebook"),
        ("🟠 Kwai", "kwai"),
        ("❎ X / Twitter", "twitter"),
        ("🎮 Twitch", "twitch"),
        ("💼 LinkedIn", "linkedin"),
        ("📌 Pinterest", "pinterest"),
        ("🧵 Threads", "threads"),
        ("🦋 Bluesky", "bluesky"),
        ("👽 Reddit", "reddit"),
        ("💬 WhatsApp", "whatsapp"),
        ("📺 Rumble", "rumble"),
        ("🎮 Trovo", "trovo"),
        ("🎬 SnackVideo", "snackvideo"),
        ("🎨 Dribbble", "dribbble"),
        ("⭐ Trustpilot", "trustpilot"),
        ("📍 Google", "google"),
        ("🌐 Tráfego para sites", "site"),
        ("🪙 CoinMarketCap", "coinmarketcap"),
        ("🎲 Roblox", "roblox"),
    ]

    botoes = []
    for i in range(0, len(redes), 2):
        linha = [
            InlineKeyboardButton(
                nome,
                callback_data=f"rede_{rede}",
                api_kwargs={"style": "success"},
            )
            for nome, rede in redes[i:i + 2]
        ]
        botoes.append(linha)

    botoes.append([
        InlineKeyboardButton(
            "💰 Meu saldo",
            callback_data="saldo",
            api_kwargs={"style": "danger"},
        ),
        InlineKeyboardButton(
            "📦 Meus pedidos",
            callback_data="pedidos",
            api_kwargs={"style": "danger"},
        ),
    ])
    botoes.append([
        InlineKeyboardButton(
            "🎧 Suporte",
            callback_data="suporte",
            api_kwargs={"style": "danger"},
        )
    ])

    return InlineKeyboardMarkup(botoes)
def menu_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💰 Configurar lucro (%)",
            callback_data="admin_lucro"
        , api_kwargs={"style": "primary"})],
        [InlineKeyboardButton(
            "📊 Consultar configuração",
            callback_data="admin_config"
        , api_kwargs={"style": "primary"})],
        [InlineKeyboardButton(
            "⬅️ Menu principal",
            callback_data="inicio"
        , api_kwargs={"style": "primary"})],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        save_user(user.id, user.username)

    mensagem = update.effective_message
    if not mensagem:
        return

    foto_id = context.bot_data.get("viral_bot_file_id")

    try:
        if foto_id:
            await mensagem.reply_photo(
                photo=foto_id,
                reply_markup=menu_principal(),
            )
        else:
            caminho = Path(__file__).resolve().parent / "assets" / "viral_bot.png"
            with caminho.open("rb") as foto:
                enviado = await mensagem.reply_photo(
                    photo=foto,
                    reply_markup=menu_principal(),
                )
            context.bot_data["viral_bot_file_id"] = enviado.photo[-1].file_id
    except Exception:
        logger.exception("Falha ao enviar a imagem do VIRAL BOT.")
        await mensagem.reply_text(
            "VIRAL BOT",
            reply_markup=menu_principal(),
        )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user or user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "⛔ Você não tem permissão para acessar a administração."
        )
        return

    await update.effective_message.reply_text(
        "👑 PAINEL ADMINISTRATIVO",
        reply_markup=menu_admin(),
    )


def filtrar_servicos(servicos, rede):
    palavras = {
        "instagram": ("instagram", "insta", "ig "),
        "tiktok": ("tiktok", "tik tok", "tktk"),
        "youtube": ("youtube", "yt ", "ytb"),
        "telegram": ("telegram", "tg "),
        "facebook": ("facebook", "fb ", "face |", "face/"),
        "kwai": ("kwai",),
        "twitter": ("twitter", "twitter/x", "x/twitter"),
        "twitch": ("twitch",),
        "linkedin": ("linkedin", "linked in"),
        "pinterest": ("pinterest",),
        "threads": ("threads",),
        "bluesky": ("bluesky", "blue sky"),
        "reddit": ("reddit",),
        "whatsapp": ("whatsapp", "whats app"),
        "rumble": ("rumble",),
        "trovo": ("trovo",),
        "snackvideo": ("snackvideo", "snack video"),
        "dribbble": ("dribbble", "dribble"),
        "trustpilot": ("trustpilot",),
        "google": ("google", "gmb", "maps", "avaliação", "avaliacao", "review", "reviews", "business", "perfil da empresa"),
        "site": ("tráfego para site", "trafego para site",
                 "website traffic", "site traffic", "visitas ao site"),
        "coinmarketcap": ("coinmarketcap", "coin market cap"),
        "roblox": ("roblox",),
    }

    termos = palavras.get(rede, (rede.lower(),))
    resultado = []

    for servico in servicos:
        if not isinstance(servico, dict):
            continue

        texto = (
            str(servico.get("name", "")) + " " +
            str(servico.get("category", ""))
        ).lower()

        if any(termo in texto for termo in termos):
            resultado.append(servico)

    return resultado

_catalogo_cache = None
_catalogo_cache_atualizado = 0.0

async def carregar_catalogo(query, context, rede, pagina=0):
    global _catalogo_cache, _catalogo_cache_atualizado
    try:
        agora = time.monotonic()
        if (
            _catalogo_cache is None
            or agora - _catalogo_cache_atualizado >= 300
        ):
            api = SMMClient()
            servicos_novos = await api.listar_servicos()
            if not isinstance(servicos_novos, list):
                raise SMMAPIError("Resposta inesperada ao consultar os serviços.")
            _catalogo_cache = servicos_novos
            _catalogo_cache_atualizado = time.monotonic()

        servicos = filtrar_servicos(_catalogo_cache, rede)
    except SMMAPIError:
        logger.exception("Falha ao consultar catálogo SMM")
        await query.edit_message_text(
            "Não foi possível carregar os serviços agora. Tente novamente.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Menu", callback_data="inicio", api_kwargs={"style": "primary"})
            ]]),
        )
        return

    if not servicos:
        await query.edit_message_text(
            f"Nenhum serviço encontrado para {rede.title()}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Voltar", callback_data="inicio", api_kwargs={"style": "primary"})
            ]]),
        )
        return

    por_pagina = 8
    total_paginas = (len(servicos) + por_pagina - 1) // por_pagina
    pagina = max(0, min(pagina, total_paginas - 1))
    inicio = pagina * por_pagina
    lista = servicos[inicio:inicio + por_pagina]
    botoes = []

    for s in lista:
        try:
            custo = float(s.get("rate", 0))
            sid = str(s.get("service", ""))
            cotacao = 1 if sid == "922" else USD_BRL_RATE
            preco = calcular_preco(custo * cotacao, get_profit_percent())
            if sid == "922":
                preco = calcular_preco(8, get_profit_percent())
            nome = str(s.get("name", "Serviço"))[:48]
            botoes.append([InlineKeyboardButton(
                f"{nome} — R$ {preco}",
                callback_data=f"servico:{sid}:{rede}:{pagina}"
            , api_kwargs={"style": "primary"})])
        except (ValueError, TypeError):
            continue

    navegacao = []
    if pagina > 0:
        navegacao.append(InlineKeyboardButton(
            "⬅️ Anterior",
            callback_data=f"catalogo:{rede}:{pagina - 1}"
        , api_kwargs={"style": "primary"}))
    if pagina + 1 < total_paginas:
        navegacao.append(InlineKeyboardButton(
            "Próxima ➡️",
            callback_data=f"catalogo:{rede}:{pagina + 1}"
        , api_kwargs={"style": "primary"}))
    if navegacao:
        botoes.append(navegacao)

    botoes.append([InlineKeyboardButton(
        "⬅️ Menu principal", callback_data="inicio"
    , api_kwargs={"style": "primary"})])

    texto = (
        f"📋 CATÁLOGO {rede.upper()}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🚀 Impulsione seu perfil com os serviços disponíveis!\n\n"
        "💰 INFORMAÇÕES SOBRE OS PREÇOS\n"
        "📌 Os valores exibidos são referentes a 1.000 unidades.\n"
        "🔢 Escolha a quantidade desejada, de 50 a 1.000 unidades, respeitando os limites de cada serviço.\n"
        "🧮 O valor total será calculado conforme a quantidade escolhida.\n\n"
        f"🛍️ {len(servicos)} serviços disponíveis\n"
        f"📄 Página {pagina + 1} de {total_paginas}\n\n"
        "👇 Escolha o serviço desejado abaixo."
    )
    mensagem = query.message
    teclado = InlineKeyboardMarkup(botoes)
    if mensagem and mensagem.photo:
        await query.edit_message_caption(
            caption=texto,
            reply_markup=teclado,
        )
    else:
        await query.edit_message_text(
            texto,
            reply_markup=teclado,
        )




async def iniciar_compra(query, context, service_id, rede, pagina):
    try:
        api = SMMClient()
        servicos = await api.listar_servicos()
        servico = next(
            (s for s in servicos
             if isinstance(s, dict)
             and str(s.get("service")) == str(service_id)),
            None,
        )

        if not servico:
            context.user_data.pop("compra", None)
            await query.edit_message_text(
                "⚠️ SERVIÇO ESGOTADO\n\n"
                "Este serviço não está mais disponível no fornecedor.\n"
                "Escolha outro serviço ou tente novamente mais tarde.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🔄 Atualizar catálogo",
                        callback_data=f"catalogo:{rede}:{pagina}"
                    , api_kwargs={"style": "primary"})
                ], [
                    InlineKeyboardButton(
                        "🏠 Menu principal",
                        callback_data="inicio"
                    , api_kwargs={"style": "primary"})
                ]]),
            )
            return

        context.user_data["compra"] = {
            "etapa": "link",
            "service_id": str(service_id),
            "rede": rede,
            "pagina": int(pagina),
            "nome": str(servico.get("name", "Serviço")),
            "rate": "8" if str(service_id) == "922" else str(servico.get("rate", "0")),
            "min": int(servico.get("min", 1)),
            "max": int(servico.get("max", 200000)),
            "custom_comments": (
                (
                    str(servico.get("type", "")).strip().lower() == "custom comments"
                    or str(service_id) == "922"
                )
            ),
            "comments": "",
        }

        await query.edit_message_text(
            f"🛒 COMPRA: {servico.get('name', 'Serviço')}\n\n"
            "Envie o link do perfil, publicação ou página onde deseja aplicar o serviço.\n\n"
            "Envie /cancelar para desistir."
        )
    except Exception:
        logger.exception("Erro ao iniciar compra")
        await query.edit_message_text("Não consegui iniciar a compra. Tente novamente.")


async def processar_texto_compra(update, context):
    compra = context.user_data.get("compra")
    if not compra:
        return

    mensagem = update.effective_message
    if mensagem is None:
        return

    texto = (mensagem.text or "").strip()

    if texto == "/cancelar":
        context.user_data.pop("compra", None)
        await mensagem.reply_text("Compra cancelada.")
        return

    if compra["etapa"] == "link":
        link = texto.strip()

        if link.startswith("@"):
            usuario = link[1:].strip()
            if not usuario or len(usuario) > 30 or not all(
                c.isalnum() or c in "._" for c in usuario
            ):
                await mensagem.reply_text(
                    "Usuario invalido. Envie um @usuario valido."
                )
                return
            link = f"https://www.instagram.com/{usuario}/"
        else:
            url = urlparse(link)
            if url.scheme not in ("http", "https") or not url.netloc:
                await mensagem.reply_text(
                    "Envie um link completo ou um usuario com @."
                )
                return

        compra["link"] = link
        compra["etapa"] = "quantidade"
        await mensagem.reply_text(
            f"Informe a quantidade desejada.\n"
            f"Minimo: {compra['min']}\n"
            f"Maximo: {compra['max']}"
        )
        return

    if compra["etapa"] == "quantidade":
        try:
            quantidade = int(texto)
        except ValueError:
            await mensagem.reply_text("Envie apenas a quantidade em números inteiros.")
            return

        if not compra["min"] <= quantidade <= compra["max"]:
            await mensagem.reply_text(
                f"Quantidade fora dos limites. Use de {compra['min']} a {compra['max']}."
            )
            return

        cotacao = Decimal("1") if str(compra["service_id"]) == "922" else Decimal(str(USD_BRL_RATE))
        taxa = Decimal(str(compra["rate"])) * cotacao
        custo = (taxa * Decimal(quantidade) / Decimal(1000)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if str(compra["service_id"]) == "922":
            total = calcular_preco(
                Decimal(str(compra["rate"])) * Decimal(quantidade),
                get_profit_percent()
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            total = calcular_preco_por_mil(
                taxa, quantidade, get_profit_percent()
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if total <= 0:
            await mensagem.reply_text("Não foi possível calcular o preço. Compra interrompida.")
            context.user_data.pop("compra", None)
            return

        compra["quantidade"] = quantidade
        compra["custo"] = str(custo)
        compra["total"] = str(total)
        if compra.get("custom_comments"):
            compra["etapa"] = "comments"
            await mensagem.reply_text(
                f"Envie exatamente {quantidade} comentário(s), um por linha.\n"
                "Use comentários legítimos e autorizados.\n"
                "Envie /cancelar para desistir."
            )
            return
        compra["etapa"] = "email"

        await mensagem.reply_text(
            f"📦 Serviço: {compra['nome']}\n"
            f"🔢 Quantidade: {quantidade}\n"
            f"💰 Total: R$ {total:.2f}\n\n"
            "Informe seu e-mail para gerar o Pix no Mercado Pago.\n"
            "Envie /cancelar para desistir."
        )
        return

    if compra["etapa"] == "comments":
        linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
        quantidade = int(compra["quantidade"])
        if len(linhas) != quantidade:
            await mensagem.reply_text(
                f"Envie exatamente {quantidade} comentário(s), um por linha."
            )
            return
        if any(len(linha) > 1000 for linha in linhas):
            await mensagem.reply_text(
                "Cada comentário deve ter no máximo 1000 caracteres."
            )
            return
        compra["comments"] = "\n".join(linhas)
        compra["etapa"] = "email"
        await mensagem.reply_text(
            "Comentários recebidos.\n\n"
            "Informe seu e-mail para gerar o Pix no Mercado Pago.\n"
            "Envie /cancelar para desistir."
        )
        return

    if compra["etapa"] == "confirmacao_publico":
        await mensagem.reply_text(
            "Use os botões acima para continuar ou cancelar a compra."
        )
        return

    if compra["etapa"] == "email":
        if "@" not in texto or "." not in texto.rsplit("@", 1)[-1]:
            await mensagem.reply_text(
                "E-mail inválido. Informe um e-mail válido."
            )
            return

        compra["email"] = texto
        compra["etapa"] = "confirmacao_publico"

        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Estou ciente — Continuar",
                    callback_data="confirmar_publico",
                api_kwargs={"style": "primary"})
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancelar compra",
                    callback_data="cancelar_compra",
                api_kwargs={"style": "danger"})
            ],
        ])

        await mensagem.reply_text(
            "⚠️ ATENÇÃO! ANTES DE COMPRAR\n\n"
            "🔓 Para evitar problemas na entrega, mantenha sua conta "
            "ou perfil PÚBLICO.\n\n"
            "Contas privadas podem impedir a execução de seguidores, "
            "curtidas, visualizações e outros serviços.\n\n"
            "Confira também se o link informado está correto.\n\n"
            "Você confirma que está ciente e deseja continuar?",
            reply_markup=teclado,
        )
        return


async def finalizar_compra_pix(mensagem, context, compra, usuario_id):
    order_id = None

    try:
        order_id = criar_pedido_local(
            usuario_id,
            compra["service_id"],
            compra["link"],
            compra["quantidade"],
            compra["custo"],
            compra["total"],
            comments=compra.get("comments", ""),
        )

        payment_local_id = criar_pagamento_local(
            usuario_id, order_id, compra["total"], "mercadopago"
        )

        pix = await MercadoPago().criar_pix(
            compra["total"],
            f"Pedido #{order_id} - {compra['nome']}",
            compra["email"],
        )

        associar_pagamento_externo(
            payment_local_id, pix["payment_id"]
        )

        context.user_data.pop("compra", None)

        teclado = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🔎 Verificar pagamento",
                callback_data=f"pixcheck:{payment_local_id}",
            api_kwargs={"style": "primary"})
        ]])

        await mensagem.reply_text(
            f"✅ COBRANÇA PIX GERADA\n\n"
            f"Pedido: #{order_id}\n"
            f"Valor: R$ {Decimal(compra['total']):.2f}\n\n"
            "Copie o código Pix abaixo e pague no seu banco:\n\n"
            f"<code>{pix['pix_copia_cola']}</code>\n\n"
            "Depois do pagamento, toque em Verificar pagamento.",
            parse_mode="HTML",
            reply_markup=teclado,
        )

    except MercadoPagoError:
        logger.exception("Falha ao gerar Pix")
        context.user_data.pop("compra", None)

        if order_id is not None:
            atualizar_status_pedido(order_id, "pix_creation_failed")

        await mensagem.reply_text(
            "Não consegui gerar o Pix. Nenhum pedido foi enviado ao fornecedor. "
            "Se houve algum problema com a cobrança, contate o suporte "
            "antes de tentar novamente."
        )

    except Exception:
        logger.exception("Erro ao preparar cobrança")
        context.user_data.pop("compra", None)

        if order_id is not None:
            atualizar_status_pedido(order_id, "pix_creation_failed")

        await mensagem.reply_text(
            "Ocorreu um erro ao preparar a cobrança. "
            "Confira o menu de pedidos ou contate o suporte antes de tentar novamente."
        )
        if "@" not in texto or "." not in texto.rsplit("@", 1)[-1]:
            await mensagem.reply_text("E-mail inválido. Informe um e-mail válido.")
            return

        usuario = update.effective_user
        order_id = None

        try:
            order_id = criar_pedido_local(
                usuario.id,
                compra["service_id"],
                compra["link"],
                compra["quantidade"],
                compra["custo"],
                compra["total"],
            comments=compra.get("comments", ""),
            )
            payment_local_id = criar_pagamento_local(
                usuario.id, order_id, compra["total"], "mercadopago"
            )

            pix = await MercadoPago().criar_pix(
                compra["total"],
                f"Pedido #{order_id} - {compra['nome']}",
                texto,
            )

            associar_pagamento_externo(
                payment_local_id, pix["payment_id"]
            )

            context.user_data.pop("compra", None)

            teclado = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔎 Verificar pagamento",
                    callback_data=f"pixcheck:{payment_local_id}",
                api_kwargs={"style": "primary"})
            ]])

            await mensagem.reply_text(
                f"✅ COBRANÇA PIX GERADA\n\n"
                f"Pedido: #{order_id}\n"
                f"Valor: R$ {Decimal(compra['total']):.2f}\n\n"
                "Copie o código Pix abaixo e pague no seu banco:\n\n"
                f"<code>{pix['pix_copia_cola']}</code>\n\n"
                "Depois do pagamento, toque em Verificar pagamento.",
                parse_mode="HTML",
                reply_markup=teclado,
            )

        except MercadoPagoError:
            logger.exception("Falha ao gerar Pix")
            if order_id is not None:
                atualizar_status_pedido(order_id, "pix_creation_failed")
            await mensagem.reply_text(
                "Não consegui gerar o Pix. Nenhum pedido foi enviado ao fornecedor. "
                "Tente novamente mais tarde ou contate o suporte."
            )
        except Exception:
            logger.exception("Erro ao preparar cobrança")
            if order_id is not None:
                atualizar_status_pedido(order_id, "pix_creation_failed")
            await mensagem.reply_text(
                "Ocorreu um erro ao preparar a cobrança. "
                "Confira o menu de pedidos ou contate o suporte."
            )




async def enviar_pix_recarga(mensagem, context, telegram_id, email, valor):
    payment_local_id = None
    try:
        payment_local_id = criar_recarga_local(
            telegram_id, valor, "mercadopago"
        )
        mp = MercadoPago()
        resultado = await mp.criar_pix(
            valor, f"Recarga de saldo Telegram #{telegram_id}", email
        )
        associar_pagamento_externo(
            payment_local_id, resultado["payment_id"]
        )
        context.user_data.pop("recarga", None)

        texto_pix = resultado["pix_copia_cola"]
        await mensagem.reply_text(
            f"💠 RECARGA DE SALDO\n\n"
            f"Valor: R$ {valor:.2f}\n"
            f"Status: aguardando pagamento\n\n"
            f"Pix copia e cola:\n\n{texto_pix}\n\n"
            "Após pagar, toque em Verificar pagamento.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔎 Verificar pagamento",
                    callback_data=f"pixcheck:{payment_local_id}",
                api_kwargs={"style": "primary"})
            ], [
                InlineKeyboardButton(
                    "💰 Meu saldo", callback_data="saldo"
                , api_kwargs={"style": "primary"})
            ]]),
        )
    except Exception:
        logger.exception("Erro ao gerar Pix de recarga")
        context.user_data.pop("recarga", None)
        await mensagem.reply_text(
            "Não foi possível gerar a recarga Pix. "
            "Confira seu saldo antes de tentar novamente."
        )


async def verificar_recarga_pix(query, payment_local_id, pagamento):
    usuario = query.from_user
    external_id = str(pagamento["external_id"] or "")
    if not external_id:
        await query.edit_message_text(
            "Esta recarga ainda não possui um Pix válido. Contate o suporte."
        )
        return

    try:
        resultado = await MercadoPago().consultar_pagamento(external_id)
    except Exception:
        logger.exception("Erro ao consultar Pix de recarga")
        await query.edit_message_text(
            "Não foi possível consultar o Pix agora. Tente novamente."
        )
        return

    if resultado.get("payment_id") != external_id:
        await query.edit_message_text("Não foi possível validar a cobrança.")
        return

    recebido = Decimal(str(resultado.get("valor", "0"))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    esperado = Decimal(str(pagamento["amount"])).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    if resultado.get("status") != "approved":
        await query.edit_message_text(
            "⏳ Recarga ainda não aprovada.\n"
            f"Status: {resultado.get('status') or 'pendente'}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔎 Verificar novamente",
                    callback_data=f"pixcheck:{payment_local_id}",
                api_kwargs={"style": "primary"})
            ], [
                InlineKeyboardButton("💰 Meu saldo", callback_data="saldo", api_kwargs={"style": "primary"})
            ]]),
        )
        return

    if recebido != esperado or resultado.get("metodo") != "pix":
        await query.edit_message_text(
            "⚠️ Os dados do pagamento não correspondem à recarga. "
            "O saldo não foi alterado. Contate o suporte."
        )
        return

    creditado = creditar_recarga_aprovada(
        payment_local_id, usuario.id, external_id
    )
    saldo = consultar_saldo_usuario(usuario.id)

    if creditado:
        await query.edit_message_text(
            "✅ RECARGA CONFIRMADA!\n\n"
            f"Valor adicionado: R$ {esperado:.2f}\n"
            f"Saldo disponível: R$ {saldo:.2f}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💰 Meu saldo", callback_data="saldo", api_kwargs={"style": "primary"})
            ], [
                InlineKeyboardButton("🏠 Menu principal", callback_data="inicio", api_kwargs={"style": "primary"})
            ]]),
        )
    else:
        # Pode ser uma consulta repetida após o crédito já ter ocorrido.
        pagamento_atual = buscar_pagamento(payment_local_id)
        if (
            pagamento_atual
            and int(pagamento_atual["telegram_id"]) == usuario.id
            and pagamento_atual["order_id"] is None
            and pagamento_atual["status"] == "approved"
            and str(pagamento_atual["external_id"]) == external_id
        ):
            await query.edit_message_text(
                "✅ Esta recarga já foi confirmada anteriormente.\n\n"
                f"Saldo disponível: R$ {saldo:.2f}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💰 Meu saldo", callback_data="saldo", api_kwargs={"style": "primary"})
                ]]),
            )
        else:
            await query.edit_message_text(
                "Não foi possível creditar esta recarga. "
                "Contate o suporte antes de fazer outro pagamento."
            )

async def processar_compra_com_saldo(query, context):
    usuario_id = query.from_user.id
    compra = context.user_data.get("compra")

    if not compra or compra.get("etapa") != "escolha_pagamento":
        await query.edit_message_text(
            "Esta compra expirou. Inicie novamente pelo catálogo."
        )
        return

    compra["etapa"] = "processando_saldo"
    await query.edit_message_text("Processando pagamento com seu saldo...")

    order_id = None
    try:
        order_id, resultado = criar_pedido_com_saldo(
            usuario_id,
            compra["service_id"],
            compra["link"],
            compra["quantidade"],
            compra["custo"],
            compra["total"],
            comments=compra.get("comments", ""),
        )

        if resultado == "saldo_insuficiente":
            compra["etapa"] = "escolha_pagamento"
            saldo = consultar_saldo_usuario(usuario_id)
            botoes = [[InlineKeyboardButton(
                "💠 Pagar com Pix",
                callback_data="comprar_com_pix",
            api_kwargs={"style": "success"})]]
            await query.message.reply_text(
                f"Saldo insuficiente. Disponível: R$ {saldo:.2f}.",
                reply_markup=InlineKeyboardMarkup(botoes),
            )
            return

        if resultado != "ok" or order_id is None:
            context.user_data.pop("compra", None)
            await query.message.reply_text(
                "Não foi possível registrar o pedido. Contate o suporte "
                "antes de tentar novamente."
            )
            return

        context.user_data.pop("compra", None)

        with connect() as db:
            pedido = db.execute(
                "SELECT * FROM orders WHERE id = ? AND telegram_id = ?",
                (order_id, usuario_id),
            ).fetchone()

        if not pedido:
            await query.message.reply_text(
                f"Pedido #{order_id} registrado, mas precisa de análise do suporte."
            )
            return

        try:
            resposta = await SMMClient().criar_pedido(
                pedido["service_id"],
                pedido["link"],
                pedido["quantity"],
                comments=(pedido["comments"] or None) if str(pedido["service_id"]) != "922" else None,
            )
            atualizar_status_pedido(
                order_id, "processing", str(resposta["order"])
            )
            await query.message.reply_text(
                f"✅ Pagamento com saldo confirmado!\n"
                f"📦 Pedido #{order_id} enviado ao fornecedor.\n"
                "Status: em processamento."
            )
        except Exception as exc:
            logger.exception(
                "Pagamento por saldo concluído; erro no pedido %s", order_id
            )
            if (
                isinstance(exc, SMMAPIError)
                and "not_enough_funds" in str(exc).lower()
            ):
                atualizar_status_pedido(order_id, "pending_supplier_funds")
                await query.message.reply_text(
                    f"✅ Saldo debitado e pedido #{order_id} registrado.\n"
                    "⏳ O fornecedor está sem saldo; o pedido ficou pendente."
                )
            else:
                atualizar_status_pedido(order_id, "paid_supplier_error")
                await query.message.reply_text(
                    f"⚠️ O saldo foi debitado e o pedido #{order_id} foi registrado, "
                    "mas o fornecedor não confirmou o envio. Contate o suporte "
                    "e não tente comprar novamente."
                )

    except Exception:
        logger.exception("Erro no pagamento com saldo")
        if order_id is None:
            compra["etapa"] = "escolha_pagamento"
        await query.message.reply_text(
            "Ocorreu um erro ao processar o pagamento com saldo. "
            "Confira seus pedidos e o saldo antes de tentar novamente."
        )


async def verificar_pix(query, context, payment_local_id):
    usuario = query.from_user

    try:
        pagamento = buscar_pagamento(payment_local_id)

        if not pagamento or int(pagamento["telegram_id"]) != usuario.id:
            await query.edit_message_text("Pagamento não encontrado.")
            return

        if pagamento["order_id"] is None:
            await verificar_recarga_pix(query, payment_local_id, pagamento)
            return
        order_id = int(pagamento["order_id"])

        if pagamento["status"] == "approved":
            with connect() as db:
                pedido = db.execute(
                    "SELECT status FROM orders WHERE id = ? AND telegram_id = ?",
                    (order_id, usuario.id),
                ).fetchone()

            estado = pedido["status"] if pedido else "desconhecido"
            await query.edit_message_text(
                f"Pagamento já confirmado.\nPedido #{order_id}: {estado}."
            )
            return

        if not pagamento["external_id"]:
            await query.edit_message_text(
                "Esta cobrança ainda não possui um identificador Pix válido. "
                "Contate o suporte antes de pagar."
            )
            return

        mp = MercadoPago()
        resultado = await mp.consultar_pagamento(pagamento["external_id"])

        if resultado["payment_id"] != str(pagamento["external_id"]):
            await query.edit_message_text("Não foi possível validar a cobrança.")
            return

        valor_recebido = Decimal(str(resultado.get("valor", "0"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        valor_esperado = Decimal(str(pagamento["amount"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if resultado["status"] != "approved":
            await query.edit_message_text(
                f"Pagamento ainda não aprovado.\nStatus: {resultado['status'] or 'pendente'}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🔎 Verificar novamente",
                        callback_data=f"pixcheck:{payment_local_id}",
                    api_kwargs={"style": "primary"})
                ]]),
            )
            return

        if valor_recebido != valor_esperado:
            await query.edit_message_text(
                "O valor confirmado não corresponde ao pedido. "
                "O pedido não foi enviado. Contate o suporte."
            )
            return

        if resultado.get("metodo") != "pix":
            await query.edit_message_text(
                "O método de pagamento não corresponde ao Pix esperado. "
                "O pedido não foi enviado."
            )
            return

        if not marcar_pagamento_aprovado(
            payment_local_id, pagamento["external_id"]
        ):
            await query.edit_message_text(
                "O pagamento já está sendo processado. Aguarde."
            )
            return

        # Reserva o pedido atomicamente para impedir envios duplicados.
        with connect() as db:
            reserva = db.execute(
                """
                UPDATE orders SET status = 'submitting'
                WHERE id = ? AND telegram_id = ?
                  AND status = 'pending_payment'
                """,
                (order_id, usuario.id),
            )
            reservado = reserva.rowcount == 1

        if not reservado:
            await query.edit_message_text(
                "Pagamento confirmado, mas o pedido já está em processamento "
                "ou precisa de análise do suporte. Não tente pagar novamente."
            )
            return

        with connect() as db:
            pedido = db.execute(
                "SELECT * FROM orders WHERE id = ? AND telegram_id = ?",
                (order_id, usuario.id),
            ).fetchone()

        if not pedido:
            await query.edit_message_text(
                "Pagamento confirmado. O pedido precisa de análise do suporte."
            )
            return

        try:
            fornecedor = SMMClient()
            resposta = await fornecedor.criar_pedido(
                pedido["service_id"],
                pedido["link"],
                pedido["quantity"],
                comments=(pedido["comments"] or None) if str(pedido["service_id"]) != "922" else None,
            )
            atualizar_status_pedido(
                order_id, "processing", str(resposta["order"])
            )
            await query.edit_message_text(
                f"✅ Pagamento confirmado!\n"
                f"📦 Pedido #{order_id} enviado ao fornecedor.\n"
                f"Status: em processamento."
            )
        except Exception as exc:
            logger.exception(
                "Pagamento aprovado; erro ao enviar pedido %s", order_id
            )

            if (
                isinstance(exc, SMMAPIError)
                and "not_enough_funds" in str(exc).lower()
            ):
                atualizar_status_pedido(
                    order_id, "pending_supplier_funds"
                )
                await query.edit_message_text(
                    f"✅ Pagamento confirmado para o pedido #{order_id}.\n"
                    "⏳ O fornecedor está sem saldo suficiente.\n"
                    "Seu pedido ficou pendente e será tentado novamente "
                    "automaticamente. Você não precisa pagar novamente."
                )
            else:
                atualizar_status_pedido(order_id, "paid_supplier_error")
                await query.edit_message_text(
                    f"✅ Pagamento confirmado para o pedido #{order_id}.\n"
                    "⚠️ O fornecedor não confirmou a criação do pedido.\n"
                    "O caso precisa de verificação para evitar duplicidade. "
                    "Você não precisa pagar novamente."
                )

    except MercadoPagoError:
        logger.exception("Erro ao consultar Pix")
        await query.edit_message_text(
            "Não consegui consultar o pagamento agora. Tente novamente."
        )
    except Exception:
        logger.exception("Erro na verificação do pagamento")
        await query.edit_message_text(
            "Ocorreu um erro ao verificar o pagamento. Contate o suporte."
        )


async def mostrar_servico(query, context, service_id, rede, pagina):
    try:
        api = SMMClient()
        servicos = await api.listar_servicos()
        servico = next(
            (s for s in servicos
             if isinstance(s, dict)
             and str(s.get("service")) == service_id),
            None,
        )
        if not servico:
            await query.edit_message_text(
                "Serviço não encontrado. Abra o catálogo novamente.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⬅️ Catálogo",
                        callback_data=f"catalogo:{rede}:{pagina}"
                    , api_kwargs={"style": "primary"})
                ]]),
            )
            return

        custo = float(servico.get("rate", 0))
        cotacao = 1 if str(service_id) == "922" else USD_BRL_RATE
        preco = calcular_preco(custo * cotacao, get_profit_percent())
        if str(service_id) == "922":
            preco = calcular_preco(8, get_profit_percent())
        texto = (
            f"📦 {servico.get('name', 'Serviço')}\n\n"
            f"ID: {service_id}\n"
            f"Categoria: {servico.get('category', 'Não informada')}\n"
            f"Preço: R$ {preco} por comentário\n" if str(service_id) == "922" else f"Preço: R$ {preco} por mil\n"
            f"Mínimo: {servico.get('min', 'N/I')}\n"
            f"Máximo: {servico.get('max', 'N/I')}\n\n"
            "Escolha uma opção abaixo para continuar. "
            ""
        )
        teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🛒 Comprar agora",
            callback_data=f"comprar:{service_id}:{rede}:{pagina}"
        , api_kwargs={"style": "success"})],
        [
            InlineKeyboardButton(
                "⬅️ Voltar ao catálogo",
                callback_data=f"catalogo:{rede}:{pagina}"
            , api_kwargs={"style": "primary"})
        ], [
            InlineKeyboardButton("🏠 Menu principal", callback_data="inicio", api_kwargs={"style": "primary"})
        ]])
        await query.edit_message_text(texto, reply_markup=teclado)

    except (SMMAPIError, ValueError, TypeError):
        logger.exception("Falha ao consultar detalhes do serviço")
        await query.edit_message_text(
            "Erro ao consultar os detalhes. Tente novamente.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ Voltar",
                    callback_data=f"catalogo:{rede}:{pagina}"
                , api_kwargs={"style": "primary"})
            ]]),
        )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "inicio":
        mensagem = query.message
        if mensagem and mensagem.photo:
            await query.edit_message_caption(
                caption="🤖 VIRAL BOT — MENU PRINCIPAL",
                reply_markup=menu_principal(),
            )
        else:
            await query.edit_message_text(
                "🤖 VIRAL BOT — MENU PRINCIPAL",
                reply_markup=menu_principal(),
            )

    elif data.startswith("rede_"):
        rede = data[len("rede_"):]
        await carregar_catalogo(query, context, rede, 0)
    elif data.startswith("catalogo:"):
        partes = data.split(":")
        await carregar_catalogo(query, context, partes[1], int(partes[2]))
    elif data.startswith("servico:"):
        partes = data.split(":")
        await mostrar_servico(
            query, context, partes[1], partes[2], int(partes[3])
        )
    elif data.startswith("comprar:"):
        partes = data.split(":")
        if len(partes) == 4:
            await iniciar_compra(
                query, context, partes[1], partes[2], int(partes[3])
            )
        else:
            await query.edit_message_text("Dados da compra inválidos.")

    elif data == "confirmar_publico":
        compra = context.user_data.get("compra")
        if not compra or compra.get("etapa") != "confirmacao_publico":
            await query.edit_message_text(
                "Esta confirmação expirou. Inicie a compra novamente pelo catálogo."
            )
            return
        if not compra.get("email"):
            await query.edit_message_text(
                "O e-mail da compra não foi encontrado. Inicie a compra novamente."
            )
            context.user_data.pop("compra", None)
            return

        compra["etapa"] = "escolha_pagamento"
        saldo = consultar_saldo_usuario(query.from_user.id)
        total = float(compra["total"])
        botoes = []

        if saldo >= total:
            botoes.append([InlineKeyboardButton(
                f"💰 Pagar com saldo (R$ {total:.2f})",
                callback_data="comprar_com_saldo",
            api_kwargs={"style": "success"})])

        botoes.append([InlineKeyboardButton(
            f"💠 Pagar com Pix (R$ {total:.2f})",
            callback_data="comprar_com_pix",
        api_kwargs={"style": "success"})])
        botoes.append([InlineKeyboardButton(
            "❌ Cancelar compra",
            callback_data="cancelar_compra",
        api_kwargs={"style": "danger"})])

        await query.edit_message_text(
            "💳 ESCOLHA A FORMA DE PAGAMENTO\n\n"
            f"Valor da compra: R$ {total:.2f}\n"
            f"Saldo disponível: R$ {saldo:.2f}\n\n"
            "Escolha uma opção:",
            reply_markup=InlineKeyboardMarkup(botoes),
        )

    elif data == "comprar_com_pix":
        compra = context.user_data.get("compra")
        if not compra or compra.get("etapa") != "escolha_pagamento":
            await query.edit_message_text(
                "Esta compra expirou. Inicie novamente pelo catálogo."
            )
            return
        compra["etapa"] = "processando_pix"
        await query.edit_message_text("Preparando sua cobrança Pix...")
        await finalizar_compra_pix(
            query.message, context, compra, query.from_user.id
        )

    elif data == "comprar_com_saldo":
        await processar_compra_com_saldo(query, context)
    elif data == "cancelar_compra":
        context.user_data.pop("compra", None)
        await query.edit_message_text(
            "❌ Compra cancelada.\n"
            "Nenhum pedido ou cobrança Pix foi criado."
        )

    elif data.startswith("pixcheck:"):
        partes = data.split(":")
        if len(partes) == 2 and partes[1].isdigit():
            await verificar_pix(query, context, int(partes[1]))
        else:
            await query.edit_message_text("Identificador de pagamento inválido.")

    elif data == "adicionar_saldo":
        await query.edit_message_text(
            "➕ ADICIONAR SALDO\n\nEscolha o valor da recarga:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("R$ 5", callback_data="recarga_valor:5", api_kwargs={"style": "primary"}),
                    InlineKeyboardButton("R$ 10", callback_data="recarga_valor:10", api_kwargs={"style": "primary"}),
                ],
                [
                    InlineKeyboardButton("R$ 20", callback_data="recarga_valor:20", api_kwargs={"style": "primary"}),
                    InlineKeyboardButton("R$ 50", callback_data="recarga_valor:50", api_kwargs={"style": "primary"}),
                ],
                [InlineKeyboardButton("R$ 100", callback_data="recarga_valor:100", api_kwargs={"style": "primary"})],
                [InlineKeyboardButton("⬅️ Voltar", callback_data="saldo", api_kwargs={"style": "primary"})],
            ]),
        )
    elif data.startswith("recarga_valor:"):
        partes = data.split(":")
        permitidos = {5, 10, 20, 50, 100}
        if (
            len(partes) != 2
            or not partes[1].isdigit()
            or int(partes[1]) not in permitidos
        ):
            await query.edit_message_text("Valor de recarga inválido.")
            return
        valor = int(partes[1])
        context.user_data["recarga"] = {"etapa": "email", "valor": valor}
        await query.edit_message_text(
            f"💠 Recarga de R$ {valor:.2f}\n\n"
            "Digite seu e-mail para gerar o Pix. Envie /cancelar para desistir."
        )
    elif data == "saldo":
        saldo = consultar_saldo_usuario(user_id)
        texto_saldo = (
            "💰 MEU SALDO\n\n"
            f"Saldo disponível: R$ {saldo:.2f}\n\n"
            "Adicione saldo por Pix para utilizar sua carteira."
        )
        teclado_saldo = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕  Adicionar saldo", callback_data="adicionar_saldo", api_kwargs={"style": "success"})],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="inicio", api_kwargs={"style": "primary"})],
        ])
        if query.message and query.message.photo:
            await query.edit_message_caption(
                caption=texto_saldo, reply_markup=teclado_saldo
            )
        else:
            await query.edit_message_text(
                texto_saldo, reply_markup=teclado_saldo
            )
        return
        await query.edit_message_text(
            "💰 MEU SALDO\n\n"
            f"Saldo disponível: R$ {saldo:.2f}\n\n"
            "Adicione saldo por Pix para utilizar sua carteira.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "➕ Adicionar saldo",
                    callback_data="adicionar_saldo"
                , api_kwargs={"style": "success"})],
                [InlineKeyboardButton(
                    "⬅️ Voltar",
                    callback_data="inicio"
                , api_kwargs={"style": "primary"})],
            ]),
        )
    elif data == "pedidos":
        mensagem_com_foto = bool(query.message and query.message.photo)
        with connect() as db:
            pedidos = db.execute(
                """
                SELECT id, service_id, quantity, sale_price, status, created_at
                FROM orders
                WHERE telegram_id = ?
                  AND NOT (
                      status = 'pending_payment'
                      AND datetime(created_at) <= datetime('now', '-30 minutes')
                  )
                ORDER BY id DESC
                LIMIT 10
                """,
                (int(query.from_user.id),)
            ).fetchall()

        if not pedidos:
            await (query.edit_message_caption if mensagem_com_foto else query.edit_message_text)(
                "📦 Você ainda não tem pedidos registrados.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Voltar", callback_data="inicio", api_kwargs={"style": "primary"})]
                ])
            )
        else:
            texto = "📦 SEUS PEDIDOS\n\n"
            for pedido in pedidos:
                texto += (
                    f"🧾 Pedido #{pedido['id']}\n"
                    f"🔹 Serviço: {pedido['service_id']}\n"
                    f"🔢 Quantidade: {pedido['quantity']}\n"
                    f"💰 Valor: R$ {float(pedido['sale_price']):.2f}\n"
                    f"📌 Status: {pedido['status']}\n"
                    f"📅 Data: {pedido['created_at']}\n\n"
                )
            await (query.edit_message_caption if mensagem_com_foto else query.edit_message_text)(
                texto,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Atualizar", callback_data="pedidos", api_kwargs={"style": "primary"})],
                    [InlineKeyboardButton("⬅️ Voltar", callback_data="inicio", api_kwargs={"style": "primary"})]
                ])
            )

    elif data == "suporte":
        await query.edit_message_text(
            "🎧 Entre em contato com o administrador para obter suporte.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Voltar", callback_data="inicio", api_kwargs={"style": "primary"})
            ]]),
        )

    elif data.startswith("admin_"):
        if user_id not in ADMIN_IDS:
            await query.answer("Acesso não autorizado.", show_alert=True)
            return

        if data == "admin_lucro":
            context.user_data["aguardando_lucro"] = True
            await query.edit_message_text(
                "💰 CONFIGURAR LUCRO\n\n"
                f"Porcentagem atual: {get_profit_percent():g}%\n\n"
                "Digite a nova porcentagem. Exemplo: 50\n"
                "Envie /cancelar para desistir."
            )

        elif data == "admin_config":
            await query.edit_message_text(
                f"⚙️ CONFIGURAÇÕES\n\n"
                f"Margem global: {get_profit_percent():g}%\n\n"
                "A margem será aplicada aos custos dos serviços "
                "quando a integração com a API estiver configurada.",
                reply_markup=menu_admin(),
            )

async def receber_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensagem = update.effective_message
    if not mensagem or mensagem.text is None:
        return

    recarga = context.user_data.get("recarga")
    if recarga and recarga.get("etapa") == "email":
        email = mensagem.text.strip()
        if (
            len(email) > 254
            or "@" not in email
            or "." not in email.rsplit("@", 1)[-1]
        ):
            await mensagem.reply_text("E-mail inválido. Digite um e-mail válido.")
            return
        await enviar_pix_recarga(
            mensagem, context, update.effective_user.id,
            email, recarga["valor"]
        )
        return

    if context.user_data.get("compra"):
        await processar_texto_compra(update, context)
        return

    if not context.user_data.get("aguardando_lucro"):
        return
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        context.user_data.pop("aguardando_lucro", None)
        return

    texto = mensagem.text.strip().replace(",", ".")
    try:
        percent = float(texto)
        if not 0 <= percent <= 1000:
            raise ValueError
        set_profit_percent(percent)
    except ValueError:
        await mensagem.reply_text(
            "Valor inválido. Digite uma porcentagem entre 0 e 1000."
        )
        return

    context.user_data.pop("aguardando_lucro", None)
    await mensagem.reply_text(
        f"✅ Margem global salva: {percent:g}%.\n\n"
        "A nova margem será usada nos cálculos de preço. "
        "A sincronização dos preços depende da integração com a API SMM.",
        reply_markup=menu_admin(),
    )

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("compra", None)
    context.user_data.pop("recarga", None)
    context.user_data.pop("aguardando_lucro", None)
    await update.effective_message.reply_text("Operação cancelada.")



async def worker_pedidos_pendentes(app):
    """Tenta novamente pedidos pagos recusados por falta de saldo."""
    logger.info("Worker de pedidos pendentes iniciado.")

    while True:
        try:
            with connect() as db:
                pedidos = db.execute(
                    """
                    SELECT id, telegram_id, service_id, link, quantity
                    FROM orders
                    WHERE status = 'pending_supplier_funds'
                      AND provider_order_id IS NULL
                    ORDER BY id
                    """
                ).fetchall()

            for pedido in pedidos:
                order_id = int(pedido["id"])

                # Reserva o pedido para impedir duas tentativas simultâneas.
                with connect() as db:
                    reserva = db.execute(
                        """
                        UPDATE orders
                        SET status = 'submitting'
                        WHERE id = ?
                          AND status = 'pending_supplier_funds'
                          AND provider_order_id IS NULL
                        """,
                        (order_id,),
                    )
                    reservado = reserva.rowcount == 1

                if not reservado:
                    continue

                try:
                    fornecedor = SMMClient()
                    resposta = await fornecedor.criar_pedido(
                        pedido["service_id"],
                        pedido["link"],
                        pedido["quantity"],
                comments=(pedido["comments"] or None) if str(pedido["service_id"]) != "922" else None,
                    )

                    numero_fornecedor = resposta.get("order")
                    if not numero_fornecedor:
                        raise SMMAPIError(
                            "O fornecedor não retornou o número do pedido."
                        )

                    atualizar_status_pedido(
                        order_id,
                        "processing",
                        str(numero_fornecedor),
                    )

                    await app.bot.send_message(
                        chat_id=int(pedido["telegram_id"]),
                        text=(
                            f"✅ Seu pedido #{order_id} foi enviado "
                            "ao fornecedor!\n"
                            "Status: em processamento."
                        ),
                    )
                    logger.info(
                        "Pedido %s enviado ao fornecedor: %s",
                        order_id, numero_fornecedor,
                    )

                except SMMAPIError as exc:
                    if "not_enough_funds" in str(exc).lower():
                        atualizar_status_pedido(
                            order_id, "pending_supplier_funds"
                        )
                        logger.warning(
                            "Pedido %s continua pendente: "
                            "saldo insuficiente no fornecedor.",
                            order_id,
                        )
                    else:
                        atualizar_status_pedido(
                            order_id, "paid_supplier_error"
                        )
                        logger.error(
                            "Pedido %s exige verificação manual: %s",
                            order_id, exc,
                        )
                        for admin_id in ADMIN_IDS:
                            try:
                                await app.bot.send_message(
                                    chat_id=int(admin_id),
                                    text=(
                                        f"⚠️ Pedido #{order_id}: falha "
                                        "no fornecedor.\n"
                                        f"Motivo: {exc}\n"
                                        "Não haverá nova tentativa "
                                        "automática para evitar duplicidade."
                                    ),
                                )
                            except Exception:
                                logger.exception(
                                    "Não foi possível avisar o administrador."
                                )

                except Exception:
                    # Uma falha de comunicação pode ocorrer depois de
                    # o fornecedor criar o pedido. Não reenviar às cegas.
                    logger.exception(
                        "Pedido %s exige verificação manual",
                        order_id,
                    )
                    atualizar_status_pedido(
                        order_id, "paid_supplier_error"
                    )
                    for admin_id in ADMIN_IDS:
                        try:
                            await app.bot.send_message(
                                chat_id=int(admin_id),
                                text=(
                                    f"⚠️ Pedido #{order_id}: não foi "
                                    "possível confirmar a criação no fornecedor. "
                                    "Verifique antes de reenviar."
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "Não foi possível avisar o administrador."
                            )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erro no worker de pedidos pendentes.")

        await asyncio.sleep(60)



async def monitorar_conclusao_pedidos(app):
    """Monitora pedidos em andamento e notifica a conclusão."""
    logger.info("Monitor de conclusão de pedidos iniciado.")

    while True:
        try:
            with connect() as db:
                pedidos = db.execute("""
                    SELECT id, telegram_id, provider_order_id
                    FROM orders
                    WHERE status = 'processing'
                      AND provider_order_id IS NOT NULL
                    ORDER BY id
                """).fetchall()

            for pedido in pedidos:
                order_id = int(pedido["id"])
                provider_id = str(pedido["provider_order_id"])

                try:
                    fornecedor = SMMClient()
                    resultado = await fornecedor.consultar_pedido(provider_id)

                    if not isinstance(resultado, dict):
                        logger.warning(
                            "Resposta de status inesperada no pedido %s",
                            order_id,
                        )
                        continue

                    status = str(resultado.get("status", "")).strip().lower()

                    if status not in (
                        "completed", "complete", "concluído",
                        "concluido", "concluída", "concluida",
                    ):
                        logger.info(
                            "Pedido #%s: status do fornecedor: %s",
                            order_id,
                            resultado.get("status", "desconhecido"),
                        )
                        continue

                    # Atualização atômica: somente uma execução reserva
                    # o envio da notificação para este pedido.
                    with connect() as db:
                        reserva = db.execute("""
                            UPDATE orders
                            SET status = 'completed',
                                completion_notified = 1
                            WHERE id = ?
                              AND status = 'processing'
                              AND completion_notified = 0
                        """, (order_id,))
                        reservado = reserva.rowcount == 1

                    if not reservado:
                        continue

                    try:
                        await app.bot.send_message(
                            chat_id=int(pedido["telegram_id"]),
                            text=(
                                f"✅ Pedido #{order_id} concluído!\n\n"
                                "A CONCLUSÃO DO SEU SERVIÇO FOI CONFIRMADA.\n"
                                ""
                                "Obrigado pela preferência!"
                            ),
                        )
                        logger.info(
                            "Cliente notificado da conclusão do pedido #%s",
                            order_id,
                        )
                    except Exception:
                        # Se o Telegram falhar, permite tentar novamente.
                        with connect() as db:
                            db.execute("""
                                UPDATE orders
                                SET completion_notified = 0
                                WHERE id = ? AND status = 'completed'
                            """, (order_id,))
                        logger.exception(
                            "Não foi possível notificar o pedido #%s",
                            order_id,
                        )

                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Erro ao consultar o pedido #%s", order_id
                    )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erro no monitor de conclusão.")

        await asyncio.sleep(60)


async def expirar_pix_pendentes(app):
    """Expira pedidos Pix não pagos há mais de 30 minutos."""
    logger.info("Rotina de expiração de Pix iniciada.")
    while True:
        try:
            with connect() as db:
                candidatos = db.execute(
                    """
                    SELECT o.id, o.telegram_id, p.id AS payment_id,
                           p.external_id
                    FROM orders o
                    JOIN payments p ON p.order_id = o.id
                    WHERE o.status = 'pending_payment'
                      AND p.status = 'pending'
                      AND datetime(o.created_at) <= datetime('now', '-30 minutes')
                    """
                ).fetchall()

            for pedido in candidatos:
                external_id = pedido["external_id"]
                if not external_id:
                    continue

                try:
                    resultado = await MercadoPago().consultar_pagamento(
                        external_id
                    )
                except Exception:
                    logger.exception(
                        "Falha ao conferir Pix do pedido #%s; mantido pendente.",
                        pedido["id"],
                    )
                    continue

                if (
                    resultado.get("payment_id") != str(external_id)
                    or resultado.get("status") == "approved"
                ):
                    continue

                with connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    atual = db.execute(
                        """
                        SELECT o.status, p.status AS payment_status
                        FROM orders o
                        JOIN payments p ON p.order_id = o.id
                        WHERE o.id = ? AND p.id = ?
                        """,
                        (pedido["id"], pedido["payment_id"]),
                    ).fetchone()

                    expirado = bool(
                        atual
                        and atual["status"] == "pending_payment"
                        and atual["payment_status"] == "pending"
                    )
                    if expirado:
                        db.execute(
                            "UPDATE orders SET status = 'expired' "
                            "WHERE id = ? AND status = 'pending_payment'",
                            (pedido["id"],),
                        )
                        db.execute(
                            "UPDATE payments SET status = 'expired' "
                            "WHERE id = ? AND status = 'pending'",
                            (pedido["payment_id"],),
                        )

                if expirado:
                    logger.info("Pedido #%s expirado.", pedido["id"])
                    try:
                        await app.bot.send_message(
                            chat_id=int(pedido["telegram_id"]),
                            text=(
                                f"⌛ O Pix do pedido #{pedido['id']} expirou "
                                "após 30 minutos sem confirmação. "
                                "Se você pagou, contate o suporte."
                            ),
                        )
                    except Exception:
                        logger.exception(
                            "Falha ao avisar sobre pedido expirado #%s.",
                            pedido["id"],
                        )
        except Exception:
            logger.exception("Erro na rotina de expiração de Pix.")

        await asyncio.sleep(60)


async def atualizar_catalogo_periodicamente(app):
    """Atualiza o cache de serviços automaticamente a cada 30 minutos."""
    global _catalogo_cache, _catalogo_cache_atualizado

    while True:
        try:
            api = SMMClient()
            servicos = await api.listar_servicos()

            if not isinstance(servicos, list):
                raise SMMAPIError(
                    "Resposta inesperada ao atualizar o catálogo."
                )

            _catalogo_cache = servicos
            _catalogo_cache_atualizado = time.monotonic()

            logger.info(
                "Catálogo atualizado automaticamente: %s serviços.",
                len(servicos),
            )
        except Exception:
            logger.exception(
                "Falha na atualização automática do catálogo; "
                "o cache anterior será preservado."
            )

        await asyncio.sleep(1800)


async def iniciar_worker(app):
    """Inicia os workers após a inicialização da aplicação."""
    app.bot_data["worker_pedidos_task"] = asyncio.create_task(
        worker_pedidos_pendentes(app),
        name="worker_pedidos_pendentes",
    )
    app.bot_data["expirar_pix_task"] = asyncio.create_task(
        expirar_pix_pendentes(app),
        name="expirar_pix_pendentes",
    )
    app.bot_data["monitor_conclusao_task"] = asyncio.create_task(
        monitorar_conclusao_pedidos(app),
        name="monitorar_conclusao_pedidos",
    )
    app.bot_data["atualizar_catalogo_task"] = asyncio.create_task(
        atualizar_catalogo_periodicamente(app),
        name="atualizar_catalogo_periodicamente",
    )
    logger.info("Workers iniciados corretamente.")


async def encerrar_workers(app):
    """Cancela e aguarda os workers no encerramento do bot."""
    tarefas = [
        app.bot_data.pop("worker_pedidos_task", None),
        app.bot_data.pop("expirar_pix_task", None),
        app.bot_data.pop("monitor_conclusao_task", None),
        app.bot_data.pop("atualizar_catalogo_task", None),
    ]

    tarefas = [tarefa for tarefa in tarefas if tarefa is not None]

    for tarefa in tarefas:
        tarefa.cancel()

    if tarefas:
        await asyncio.gather(*tarefas, return_exceptions=True)

    logger.info("Workers encerrados corretamente.")

def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "Configure TELEGRAM_BOT_TOKEN no arquivo .env antes de iniciar."
        )

    init_db()

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=120.0,
        write_timeout=120.0,
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .post_init(iniciar_worker)
        .post_shutdown(encerrar_workers)
        .build()
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .post_init(iniciar_worker)
        .post_shutdown(encerrar_workers)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("cancelar", cancelar))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        receber_texto,
    ))

    logger.info("SMM Telegram Bot iniciado.")
    app.run_polling()

if __name__ == "__main__":
    main()
