from grocery.management.historical_ingestion import (
    HistoricalIngestionCommand,
    region_scopes,
)
from grocery.source.historical_contract import HistoricalDataset, HistoricalPriceQuery


class Command(HistoricalIngestionCommand):
    help = "Fetch one reviewed-scope KAMIS market daily candidate collection."
    dataset = HistoricalDataset.MARKET

    def build_queries(self, options: dict[str, object]) -> tuple[HistoricalPriceQuery, ...]:
        return tuple(
            self.query(options, region_code=region)
            for region in region_scopes(options, required=False)
        )
