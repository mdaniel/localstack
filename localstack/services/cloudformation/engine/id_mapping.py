# we can't always determine which value serves as the physical resource ID unfortunately
# this needs to be determined manually by testing against AWS (!)

# There's also a reason that this is here instead of closer to the resources themselves.
# If the resources were compliant with the generic AWS resource provider framework that AWS provides for your own resource types, we wouldn't need this.
# For legacy resources (and even some of the ones where they are open-sourced), AWS still has a layer of "secret sauce" that defines what the actual physical resource ID is.
# An extension schema only defines the primary identifiers but not directly the physical resource ID that is generated based on those.
# Since this is therefore rather part of the cloudformation layer and *not* the resource providers responsibility, we've put the mapping closer to the cloudformation engine.
PHYSICAL_RESOURCE_ID_SPECIAL_CASES = {
    # Example
    # "AWS::ApiGateway::Resource": "/properties/ResourceId",
}
