DefinitionBlock ("fan_check.aml", "SSDT", 2, "SPARKL", "FANCHK", 1)
{
    External (\_SB.FAN0._FSL, MethodObj)
    External (\_SB.FAN0._FST, MethodObj)
    External (\_SB.FAN0.SMCF, FieldUnitObj)

    Method (FSET, 1, NotSerialized)
    {
        \_SB.FAN0._FSL (Arg0)
        Return (Zero)
    }
    Method (FGET, 0, NotSerialized)
    {
        Return (\_SB.FAN0._FST ())
    }
    Method (FRAW, 0, NotSerialized)
    {
        Return (\_SB.FAN0.SMCF)
    }
}
