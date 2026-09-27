<?php

declare(strict_types=1);

namespace DI\Test\IntegrationTest\Fixtures;

use DI\Attribute\Injectable;

/**
 * Fixture class.
 */
#[Injectable(lazy: true)]
class LazyDependency
{
    private bool $value = true;

    public function getValue(): bool
    {
        return $this->value;
    }
}
