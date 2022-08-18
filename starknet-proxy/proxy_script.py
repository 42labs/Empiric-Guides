import asyncio
import json
from starknet_py.net import AccountClient
from starknet_py.contract import Contract
from starknet_py.net.gateway_client import GatewayClient
from starknet_py.transactions.declare import make_declare_tx
from starkware.starknet.compiler.compile import get_selector_from_name

# Local network
from starknet_py.net.models import StarknetChainId


async def setup_accounts():
    local_network_client = GatewayClient(
        "http://localhost:5050", chain=StarknetChainId.TESTNET
    )
    # Deploys an account on devnet and returns an instance
    account_client = await AccountClient.create_account(
        client=local_network_client, chain=StarknetChainId.TESTNET
    )
    return local_network_client, account_client


async def setup_contracts(network_client, admin_client):
    # Declare implementation contract
    declare_tx = make_declare_tx(
        compilation_source=["contracts/Implementation_v0.cairo"]
    )
    declaration_result = await admin_client.declare(declare_tx)

    # Deploy proxy and call initializer in the constructor
    deployment_result = await Contract.deploy(
        network_client,
        compilation_source=["contracts/Proxy.cairo"],
        constructor_args=[
            declaration_result.class_hash,
            get_selector_from_name("initializer"),
            [admin_client.address],
        ],
    )
    # Wait for the transaction to be accepted
    await deployment_result.wait_for_acceptance()
    proxy = deployment_result.deployed_contract

    # Redefine the ABI so that `call` and `invoke` work
    with open("artifacts/abis/Implementation_v0.json", "r") as abi_file:
        implementation_abi = json.load(abi_file)
    proxy = Contract(
        address=proxy.address,
        abi=implementation_abi,
        client=admin_client,
    )
    return proxy


async def upgrade_proxy(admin_client, proxy_contract, new_contract_src):
    # Declare implementation contract
    declare_tx = make_declare_tx(compilation_source=[new_contract_src])
    declaration_result = await admin_client.declare(declare_tx)

    # Upgrade contract
    call = proxy_contract.functions["upgrade"].prepare(
        new_implementation=declaration_result.class_hash
    )
    await admin_client.execute(calls=call, max_fee=0)
    # If you change the ABI, update the `proxy_contract` here.


async def evil_upgrade(local_network_client, proxy_contract, new_contract_src):
    evil_client = await AccountClient.create_account(
        client=local_network_client, chain=StarknetChainId.TESTNET
    )
    await upgrade_proxy(evil_client, proxy_contract, new_contract_src)


async def main():
    local_network_client, account_client = await setup_accounts()
    proxy_contract = await setup_contracts(local_network_client, account_client)

    (proxy_admin,) = await proxy_contract.functions["getAdmin"].call()
    assert account_client.address == proxy_admin
    print("The proxy admin was set to our account:", hex(proxy_admin))

    # Note that max_fee=0 is only possible on starknet-devnet.
    # When deploying on testnet, your account_client needs to have enough funds.
    value_target = 10
    await proxy_contract.functions["setValue1"].invoke(value_target, max_fee=0)
    (value1,) = await proxy_contract.functions["getValue1"].call()
    assert value_target == value1
    print("The proxy works!")

    # Check that it's upgraded
    (old_value,) = await proxy_contract.functions["getValue2"].call()
    await upgrade_proxy(
        account_client, proxy_contract, "contracts/Implementation_v1.cairo"
    )
    (new_value,) = await proxy_contract.functions["getValue2"].call()
    assert new_value != old_value
    print("And so does upgrading!")

    await evil_upgrade(
        local_network_client, proxy_contract, "contracts/Implementation_v0.cairo"
    )
    (same_value,) = await proxy_contract.functions["getValue2"].call()
    assert new_value == same_value
    print(f"Evil couldn't upgrade the contract. The value is still {same_value}.")


if __name__ == "__main__":
    asyncio.run(main())
