# Release Checklist

## Scope and source control
- [ ] Target strategy and files confirmed
- [ ] Git working tree reviewed
- [ ] No unrelated files modified
- [ ] Strategy version updated
- [ ] Commit hash recorded
- [ ] Secrets and private data excluded

## Code correctness
- [ ] Python syntax check passed
- [ ] Freqtrade strategy loading passed
- [ ] Callback API checked against installed version
- [ ] Long / Short logic reviewed
- [ ] Entry / exit collisions reviewed
- [ ] Lookahead / data leakage checked
- [ ] Startup candle count checked

## Risk controls
- [ ] Stoploss defined
- [ ] Stake and max trades defined
- [ ] Leverage explicitly set / verified
- [ ] DCA disabled or explicitly reviewed
- [ ] Fees / funding / slippage considered
- [ ] Exchange position and margin modes verified
- [ ] Manual trading / shared-account risks reviewed

## Validation
- [ ] Backtest configuration recorded
- [ ] Backtest executed and report saved
- [ ] Long / Short results reviewed
- [ ] Out-of-sample test completed
- [ ] Parameter sensitivity checked
- [ ] Dry-run plan prepared

## Deployment approval
- [ ] Dry-run behavior reviewed
- [ ] Exchange orders and stoploss behavior verified
- [ ] User explicitly approved live deployment
- [ ] Rollback / emergency stop procedure prepared

> 勾選項目必須有證據；未實際完成不得勾選。
