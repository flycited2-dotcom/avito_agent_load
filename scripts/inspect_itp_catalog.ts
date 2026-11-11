// Read-only SKU resolution for the three stale microwaves in the Avito XML.
import { prisma } from '../src/lib/db';
async function main() {
  const rows = await prisma.product.findMany({
    where: { OR: ['MS2042DB', 'MS2044V', 'MS120W'].map(model => ({ name: { contains: model, mode: 'insensitive' as const } })) },
    select: { sku: true, name: true, part: true, vendor: true, supplierName: true },
  });
  console.log(JSON.stringify(rows));
}
main().catch(e => { console.error(e.name); process.exitCode = 1; }).finally(() => prisma.$disconnect());
