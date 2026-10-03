// Deploy OracleNetwork to Studio Net (61999) using genlayer-js directly.
// deployContract({ code }) takes the source BYTES, not a file path - this is the
// path that produced every previous successful Studio Net deployment.
import { createClient, createAccount } from 'genlayer-js';
import { studionet } from 'genlayer-js/chains';
import fs from 'fs';

const PK = process.env.DEPLOY_PK;
if (!PK) { console.error('DEPLOY_PK not set'); process.exit(1); }

const CONTRACT = '/home/administrator/oracle-network/contracts/oracle_network.py';

async function main() {
  const account = createAccount(PK);
  const client = createClient({ chain: studionet, account });

  const code = fs.readFileSync(CONTRACT);
  console.log('Contract bytes:', code.length);

  const result = await client.deployContract({
    code: new Uint8Array(code),
    args: [],
  });
  console.log('Deploy TX:', result);

  const receipt = await client.waitForTransactionReceipt({
    hash: result,
    waitUntil: 'decided',
    retries: 300,
    interval: 3000,
    fullTransaction: true,
  });

  console.log('Receipt status:', receipt?.status);

  const ca = receipt?.data?.contract_address
    ?? receipt?.contract_address
    ?? receipt?.logs?.[0]?.address;
  console.log('Contract address:', ca);
  console.log('Execution result:', JSON.stringify(receipt?.logs?.[0]?.topics?.slice(0, 3)));

  if (ca) fs.writeFileSync('/tmp/oracle_network_new.txt', ca);
}

main().catch((e) => { console.error('FAIL:', e.message); process.exit(1); });