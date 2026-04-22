#!/usr/bin/env python3
import asyncio
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metaapi_cloud_sdk import MetaApi
from metaapi_cloud_sdk.clients.metastats_client import MetastatsClient
from metaapi_cloud_sdk.clients.copyfactory_client import CopyFactoryClient

async def test_metaapi_connection():
    """Test MetaApi connection and symbol resolution"""
    
    # Load credentials
    token = os.environ.get('METAAPI_TOKEN')
    account_id = os.environ.get('METAAPI_ACCOUNT_ID')
    
    if not token or not account_id:
        print("ERROR: Missing METAAPI_TOKEN or METAAPI_ACCOUNT_ID in environment")
        return False
        
    print(f"Testing MetaApi connection...")
    print(f"Token: {token[:20]}...")
    print(f"Account ID: {account_id}")
    
    try:
        # Initialize MetaApi client
        api = MetaApi(token)
        
        # Get account
        print("Fetching account...")
        account = await api.metatrader_account_api.get_account(account_id)
        print(f"Account found: {account.id}")
        print(f"Account state: {account.state}")
        print(f"Account type: {account.type}")
        print(f"Account login: {account.login}")
        
        # Wait for synchronization if needed
        if account.state != 'DEPLOYED':
            print(f"Account not deployed, waiting for synchronization...")
            await account.wait_synchronized(timeout_in_seconds=60)
            print("Account synchronized!")
        
        # Get connection
        print("Getting RPC connection...")
        connection = account.get_rpc_connection()
        await connection.connect()
        print("Connected to MetaTrader via MetaApi!")
        
        # Wait for terminal state to synchronize
        print("Waiting for terminal state synchronization...")
        await connection.wait_for_terminal_state_sync(timeout_in_seconds=30)
        print("Terminal state synchronized!")
        
        # Test getting symbols
        print("Getting available symbols...")
        symbols = await connection.get_symbols()
        print(f"Available symbols: {symbols[:10]}...")  # Show first 10
        
        # Test our specific symbols
        test_symbols = ['GOLD.i#', 'SILVER.i#']  # From our SYMBOLS config
        for symbol in test_symbols:
            if symbol in symbols:
                print(f"✓ Symbol {symbol} found")
                
                # Try to get symbol specification
                try:
                    spec = await connection.get_symbol_specification(symbol)
                    print(f"  - Description: {spec.description}")
                    print(f"  - Currency: {spec.currency}")
                    print(f"  - Tick size: {spec.tick_size}")
                    print(f"  - Tick value: {spec.tick_value}")
                    print(f"  - Contract size: {spec.contract_size}")
                except Exception as e:
                    print(f"  - Could not get spec: {e}")
            else:
                print(f"✗ Symbol {symbol} NOT found in available symbols")
                
        # Test getting market data (ticks)
        print("\\nTesting market data...")
        for symbol in ['GOLD.i#', 'SILVER.i#']:
            if symbol in symbols:
                try:
                    # Get latest tick
                    tick = await connection.get_symbol_price_tick(symbol)
                    print(f"{symbol}: Bid={tick.bid}, Ask={tick.ask}, Time={tick.time}")
                except Exception as e:
                    print(f"{symbol}: Error getting tick - {e}")
        
        # Test getting positions
        print("\\nChecking current positions...")
        positions = await Positions.get_positions(connection)
        print(f"Current positions: {len(positions)}")
        for pos in positions:
            print(f"  - {pos.symbol}: {pos.type} {pos.volume} lots @ {pos.open_price}")
        
        # Clean up
        await connection.close()
        print("\\n✓ MetaApi connection test SUCCESSFUL")
        return True
        
    except Exception as e:
        print(f"✗ MetaApi connection test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    result = asyncio.run(test_metaapi_connection())
    sys.exit(0 if result else 1)