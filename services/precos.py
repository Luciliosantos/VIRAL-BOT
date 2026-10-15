from decimal import Decimal, ROUND_HALF_UP


def calcular_preco(custo, porcentagem):
    custo = Decimal(str(custo))
    porcentagem = Decimal(str(porcentagem))

    if custo < 0 or porcentagem < 0:
        raise ValueError(
            "Custo e porcentagem não podem ser negativos."
        )

    preco = custo * (
        Decimal("1") + porcentagem / Decimal("100")
    )

    return preco.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def calcular_preco_por_mil(custo_por_mil, quantidade, porcentagem):
    if quantidade <= 0:
        raise ValueError("A quantidade deve ser maior que zero.")

    custo = Decimal(str(custo_por_mil)) * Decimal(quantidade) / 1000

    return calcular_preco(custo, porcentagem)
